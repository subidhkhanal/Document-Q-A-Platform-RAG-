"""Pre-v1 endpoints kept so an older frontend deployment keeps working during
rollout. They are thin adapters over the v1 implementation."""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from backend.api.compat import build_v1_request, legacy_event
from backend.api.v1.documents import delete_document, generated_idempotency_key, ingest_upload
from backend.api.v1.qa import enforce_qa_limits, prepare_request, run_query
from backend.auth import Principal, get_current_principal
from backend.documents import repository as repo

router = APIRouter(prefix="/api", tags=["legacy"])


class LegacyQueryRequest(BaseModel):
    question: str
    chat_history: Optional[List[dict]] = None
    conversation_id: Optional[int] = None


@router.post("/upload/document", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: Optional[int] = Form(None),
    principal: Principal = Depends(get_current_principal),
):
    return await ingest_upload(principal, file, idempotency_key=generated_idempotency_key(), project_id=project_id,
                               background_tasks=background_tasks)


@router.post("/query")
async def query(request: Request, body: LegacyQueryRequest, principal: Principal = Depends(get_current_principal)):
    enforce_qa_limits(request, principal)
    v1_body = build_v1_request(
        query=body.question or " ", chat_history=body.chat_history, conversation_id=body.conversation_id, stream=True
    )
    qa_request = await prepare_request(principal, v1_body)
    return await run_query(request, principal, v1_body, qa_request, transform=legacy_event)


@router.delete("/documents/{doc_id}")
async def delete_document_legacy(
    doc_id: int, background_tasks: BackgroundTasks, principal: Principal = Depends(get_current_principal)
):
    return await delete_document(doc_id, background_tasks, principal)


@router.get("/stats")
async def get_stats(principal: Principal = Depends(get_current_principal)):
    docs = await repo.list_readable_documents(principal)
    return {
        "total_sources": len(docs),
        "total_chunks": sum(d["chunk_count"] for d in docs),
        "supported_formats": [".pdf", ".docx", ".txt", ".md", ".epub"],
    }
