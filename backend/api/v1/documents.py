"""Document ingestion, lifecycle and ACL endpoints (tenant and authorization are
always derived from the authenticated principal, never from request fields)."""

import hashlib
import json
import uuid
from urllib.parse import quote
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from backend.audit.log import record_event
from backend.auth import Principal, get_current_principal
from backend.authz.policy import can_manage, get_readable_document
from backend.common import rate_limit
from backend.common.metrics import metrics
from backend.config import (
    GUEST_MAX_DOCUMENTS, GUEST_MAX_UPLOAD_MB, MAX_UPLOAD_SIZE_MB, RATE_LIMIT_UPLOADS_PER_HOUR, RUN_INGESTION_WORKER,
)
from backend.db.connection import db_session
from backend.documents import repository as repo
from backend.ingestion.parsers import UnsupportedDocument, detect_type
from backend.ingestion.worker import drive_queue, kick
from backend.retrieval.hybrid import invalidate_tenant_cache
from backend.storage.object_store import get_object_store

router = APIRouter(prefix="/api/v1", tags=["documents"])


class AclUpdate(BaseModel):
    visibility: Literal["private", "tenant", "restricted"]
    users: List[int] = Field(default_factory=list, max_length=1000)
    groups: List[int] = Field(default_factory=list, max_length=1000)


async def _read_limited(file: UploadFile, max_mb: int = MAX_UPLOAD_SIZE_MB) -> bytes:
    """Read the upload, refusing anything over the size limit without buffering it all."""
    limit = max_mb * 1024 * 1024
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {max_mb} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _validate_project(principal: Principal, project_id: Optional[int]) -> None:
    if project_id is None:
        return
    async with db_session() as db:
        row = await db.fetch_one("SELECT 1 FROM projects WHERE id = $1 AND user_id = $2", project_id, principal.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")


async def ingest_upload(
    principal: Principal,
    file: UploadFile,
    *,
    idempotency_key: str,
    project_id: Optional[int] = None,
    document_id: Optional[int] = None,
    visibility: str = "private",
    metadata: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> JSONResponse:
    if visibility not in repo.VISIBILITIES:
        raise HTTPException(status_code=422, detail=f"visibility must be one of {repo.VISIBILITIES}")
    if not 8 <= len(idempotency_key) <= 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 8-200 characters")
    rate_limit.check("upload", f"user:{principal.user_id}", RATE_LIMIT_UPLOADS_PER_HOUR, 3600)
    max_mb = MAX_UPLOAD_SIZE_MB
    if principal.is_guest:
        # Public demo guests: private uploads only, smaller files, a small quota.
        visibility = "private"
        max_mb = min(max_mb, GUEST_MAX_UPLOAD_MB)
        if document_id is None and await repo.count_owned_documents(principal) >= GUEST_MAX_DOCUMENTS:
            raise HTTPException(
                status_code=403,
                detail=f"Demo guests can keep up to {GUEST_MAX_DOCUMENTS} documents. Delete one to upload another.",
            )

    client_metadata = None
    if metadata:
        try:
            parsed = json.loads(metadata)
            if not isinstance(parsed, dict):
                raise ValueError
            client_metadata = {str(k)[:100]: str(v)[:500] for k, v in list(parsed.items())[:50]}
        except ValueError:
            raise HTTPException(status_code=422, detail="metadata must be a JSON object of strings")

    await _validate_project(principal, project_id)
    content = await _read_limited(file, max_mb)
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:255]
    try:
        _, extension, mime_type = detect_type(filename, content)
    except UnsupportedDocument as e:
        raise HTTPException(status_code=415, detail=str(e))

    content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    try:
        job = await repo.create_ingestion(
            principal, filename=filename, extension=extension, mime_type=mime_type, content=content,
            content_hash=content_hash, idempotency_key=idempotency_key, project_id=project_id,
            document_id=document_id, visibility=visibility, client_metadata=client_metadata,
        )
    except repo.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used for a different file")
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Document not found")
    except repo.Forbidden:
        raise HTTPException(status_code=403, detail="Only the owner or a tenant admin can add versions")

    if job["created"]:
        kick(background_tasks)
        metrics.incr("ingestion.submitted")
        await record_event(
            "document.upload", tenant_id=principal.tenant_id, user_id=principal.user_id,
            resource_type="document", resource_id=job["document_id"],
            details={"version": job["document_version"], "job_id": job["job_id"], "content_hash": content_hash,
                     "size_bytes": len(content), "mime_type": mime_type},
        )

    body = {
        "tenant_id": principal.tenant_id,
        "document_id": job["document_id"],
        "document_version": job["document_version"],
        "job_id": job["job_id"],
        "status": job["status"],
        "filename": filename,
    }
    return JSONResponse(status_code=202 if job["created"] else 200, content=body, background=background_tasks)


@router.post("/documents", status_code=202)
async def create_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None, description="Informational JSON object; never used for authorization"),
    project_id: Optional[int] = Form(None),
    document_id: Optional[int] = Form(None, description="Upload a new version of an existing document"),
    visibility: str = Form("private"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: Principal = Depends(get_current_principal),
):
    """Accept a document for asynchronous ingestion. Returns 202 with a job id; a retry
    with the same Idempotency-Key returns the original job instead of creating work."""
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    return await ingest_upload(
        principal, file, idempotency_key=idempotency_key, project_id=project_id,
        document_id=document_id, visibility=visibility, metadata=metadata, background_tasks=background_tasks,
    )


@router.get("/ingestion/jobs/{job_id}")
async def get_ingestion_job(job_id: str, principal: Principal = Depends(get_current_principal)):
    job = await repo.get_job_for_principal(principal, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "PROCESSING" and not RUN_INGESTION_WORKER:
        # Serverless: no background loop, so a status poll moves the queue forward.
        await drive_queue(max_jobs=3, time_budget=25.0, stop_when_done=job_id)
        job = await repo.get_job_for_principal(principal, job_id)
    return job


@router.get("/documents")
async def list_documents(project_id: Optional[int] = None, principal: Principal = Depends(get_current_principal)):
    return {"documents": await repo.list_readable_documents(principal, project_id)}


@router.get("/documents/{document_id}")
async def get_document(document_id: int, principal: Principal = Depends(get_current_principal)):
    doc = await repo.get_readable_document_detail(principal, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/{document_id}/file")
async def download_document(
    document_id: int,
    version: Optional[int] = None,
    principal: Principal = Depends(get_current_principal),
):
    """Application-mediated source access: the same authorization check as retrieval,
    so the raw object can never be used to bypass the RAG boundary."""
    doc = await get_readable_document(principal, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    wanted = version or doc["active_version"]
    ver = await repo.get_version(document_id, wanted) if wanted else None
    if not ver or not ver["object_key"] or ver["status"] == "deleted":
        raise HTTPException(status_code=404, detail="File not available")
    # Failed (possibly quarantined) or still-processing versions are only for managers.
    if ver["status"] not in ("ready", "superseded") and not can_manage(principal, doc):
        raise HTTPException(status_code=404, detail="File not available")

    try:
        data = await get_object_store().get(ver["object_key"])
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail="File not available")
    except Exception:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    await record_event(
        "document.download", tenant_id=principal.tenant_id, user_id=principal.user_id,
        resource_type="document", resource_id=document_id, details={"version": wanted},
    )
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in ver["filename"])
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(ver['filename'], safe='')}"
    return Response(
        content=data, media_type=ver["mime_type"],
        headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"},
    )


async def _managed_document(principal: Principal, document_id: int) -> dict:
    doc = await repo.get_tenant_document(principal, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not can_manage(principal, doc):
        # Readers learn it exists but may not manage it; non-readers get a 404.
        if await get_readable_document(principal, document_id):
            raise HTTPException(status_code=403, detail="Only the owner or a tenant admin can manage this document")
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/{document_id}/acl")
async def get_document_acl(document_id: int, principal: Principal = Depends(get_current_principal)):
    await _managed_document(principal, document_id)
    return await repo.get_acl(document_id)


@router.put("/documents/{document_id}/acl")
async def update_document_acl(
    document_id: int, body: AclUpdate, principal: Principal = Depends(get_current_principal)
):
    """Replace the ACL. Effective on the next request: scope is resolved per query and
    the retrieval cache key includes it; the tenant's cache entries are also evicted."""
    await _managed_document(principal, document_id)
    if principal.is_guest and (body.visibility != "private" or body.users or body.groups):
        raise HTTPException(status_code=403, detail="Sharing is disabled for demo guests")
    try:
        acl = await repo.set_acl(principal, document_id, body.visibility, body.users, body.groups)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    invalidate_tenant_cache(principal.tenant_id)
    await record_event(
        "document.acl_changed", tenant_id=principal.tenant_id, user_id=principal.user_id,
        resource_type="document", resource_id=document_id,
        details={"visibility": acl["visibility"], "acl_version": acl["acl_version"],
                 "users": acl["users"], "groups": acl["groups"]},
    )
    return acl


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: int, background_tasks: BackgroundTasks, principal: Principal = Depends(get_current_principal)
):
    """Revoke access immediately; vectors, chunks and objects are purged asynchronously."""
    await _managed_document(principal, document_id)
    job_id = await repo.soft_delete_document(principal, document_id)
    if not job_id:
        raise HTTPException(status_code=404, detail="Document not found")
    invalidate_tenant_cache(principal.tenant_id)
    kick(background_tasks)
    await record_event(
        "document.deleted", tenant_id=principal.tenant_id, user_id=principal.user_id,
        resource_type="document", resource_id=document_id, details={"cleanup_job_id": job_id},
    )
    return {"success": True, "document_id": document_id, "cleanup_job_id": job_id}


@router.get("/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: str,
    context_size: int = Query(1, ge=0, le=3),
    principal: Principal = Depends(get_current_principal),
):
    chunk = await repo.get_chunk_with_context(principal, chunk_id, context_size)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk


def generated_idempotency_key() -> str:
    return f"legacy-{uuid.uuid4().hex}"
