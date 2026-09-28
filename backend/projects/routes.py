"""FastAPI router for project endpoints. Projects organise a user's documents; they
narrow retrieval but never grant access — document ACLs still apply."""

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.api.compat import build_v1_request, legacy_event
from backend.api.v1.qa import enforce_qa_limits, prepare_request, run_query
from backend.auth import Principal, get_current_principal
from backend.documents import repository as documents_repo
from backend.ingestion.worker import kick
from backend.projects import database as db
from backend.projects.models import ProjectCreate, ProjectResponse, ProjectUpdate
from backend.retrieval.hybrid import invalidate_tenant_cache

router = APIRouter(prefix="/api/projects", tags=["projects"])


def generate_slug(title: str) -> str:
    """Convert a title to a URL-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]


def _legacy_document(doc: dict) -> dict:
    """Project page document card (keeps the fields the frontend already uses)."""
    return {**doc, "source": doc["filename"]}


@router.post("", response_model=ProjectResponse)
async def create_project(request: ProjectCreate, principal: Principal = Depends(get_current_principal)):
    user_id = principal.user_id
    slug = generate_slug(request.title)
    base_slug = slug
    counter = 1
    while await db.slug_exists(slug, user_id):
        slug = f"{base_slug}-{counter}"
        counter += 1

    await db.insert_project(slug=slug, title=request.title, description=request.description, user_id=user_id)
    project = await db.get_project_by_slug(slug, user_id)
    return ProjectResponse(**project, document_count=0)


@router.get("")
async def list_projects(principal: Principal = Depends(get_current_principal)):
    projects = await db.get_all_projects(principal.user_id)
    return {"projects": projects}


@router.get("/{slug}")
async def get_project_detail(slug: str, principal: Principal = Depends(get_current_principal)):
    project = await db.get_project_by_slug(slug, principal.user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    documents = await documents_repo.list_readable_documents(principal, project["id"])
    return {**project, "documents": [_legacy_document(d) for d in documents], "document_count": len(documents)}


@router.put("/{slug}", response_model=ProjectResponse)
async def update_project(slug: str, request: ProjectUpdate, principal: Principal = Depends(get_current_principal)):
    project = await db.get_project_by_slug(slug, principal.user_id)
    if project and project["read_only"]:
        raise HTTPException(status_code=403, detail="Shared projects are read-only")
    updated = await db.update_project(
        slug=slug, title=request.title, description=request.description, user_id=principal.user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    project = await db.get_project_by_slug(slug, principal.user_id)
    return ProjectResponse(**project)


@router.delete("/{slug}")
async def delete_project(
    slug: str, background_tasks: BackgroundTasks, principal: Principal = Depends(get_current_principal)
):
    """Delete a project. Documents the caller manages are revoked immediately and
    purged asynchronously; documents owned by others are just unlinked."""
    project = await db.get_project_by_slug(slug, principal.user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["read_only"]:
        raise HTTPException(status_code=403, detail="Shared projects are read-only")

    documents = await documents_repo.list_readable_documents(principal, project["id"])
    deleted = 0
    for doc in documents:
        if doc["owner_id"] == principal.user_id or principal.is_admin:
            if await documents_repo.soft_delete_document(principal, doc["document_id"]):
                deleted += 1
    invalidate_tenant_cache(principal.tenant_id)
    kick(background_tasks)

    await db.delete_project(slug, principal.user_id)
    return {"success": True, "message": f"Project '{slug}' deleted", "documents_cleaned": deleted}


@router.post("/{slug}/query")
async def project_scoped_query(slug: str, http_request: Request, principal: Principal = Depends(get_current_principal)):
    """Legacy project-scoped query (same pipeline as POST /api/v1/qa/query)."""
    enforce_qa_limits(http_request, principal)
    payload = await http_request.json()
    project_id = await db.get_project_id_by_slug(slug, principal.user_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Project not found")
    body = build_v1_request(
        query=payload.get("question") or payload.get("query") or " ",
        chat_history=payload.get("chat_history"),
        stream=True,
    )
    qa_request = await prepare_request(principal, body, project_id=project_id)
    return await run_query(http_request, principal, body, qa_request, transform=legacy_event)
