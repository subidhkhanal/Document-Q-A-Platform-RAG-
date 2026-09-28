"""Question answering endpoint (SSE streaming or single JSON response)."""

import json
from typing import Any, AsyncIterator, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.auth import Principal, get_current_principal
from backend.common import rate_limit
from backend.common.metrics import metrics
from backend.common.request_context import request_id_var
from backend.config import DEFAULT_CANDIDATE_K, RATE_LIMIT_QA_PER_IP_PER_MINUTE, RATE_LIMIT_QA_PER_MINUTE
from backend.conversations import ConversationService
from backend.projects import database as projects_db
from backend.qa.service import QARequest, answer_stream

router = APIRouter(prefix="/api/v1", tags=["qa"])


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)


class QaQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    candidate_k: Literal[20, 50, 100] = DEFAULT_CANDIDATE_K  # bounded server-side
    stream: bool = True
    project_slug: Optional[str] = Field(None, description="Narrow retrieval to one of your projects")
    conversation_id: Optional[int] = None
    chat_history: Optional[List[ChatTurn]] = Field(None, max_length=20)


def enforce_qa_limits(request: Request, principal: Principal) -> None:
    """Per-user and per-IP rate limits plus a daily budget (protects provider credits)."""
    rate_limit.check("qa_user", f"user:{principal.user_id}", RATE_LIMIT_QA_PER_MINUTE, 60)
    rate_limit.check("qa_ip", rate_limit.client_ip(request), RATE_LIMIT_QA_PER_IP_PER_MINUTE, 60)
    rate_limit.consume_daily_budget("qa")


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def prepare_request(principal: Principal, body: QaQueryRequest, project_id: Optional[int] = None) -> QARequest:
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if project_id is None and body.project_slug:
        project_id = await projects_db.get_project_id_by_slug(body.project_slug, principal.user_id)
        if project_id is None:
            raise HTTPException(status_code=404, detail="Project not found")

    history = [t.model_dump() for t in body.chat_history] if body.chat_history else None
    if body.conversation_id is not None:
        if not await ConversationService.verify_ownership(body.conversation_id, principal.user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = await ConversationService.get_recent_history(body.conversation_id, user_id=principal.user_id)

    return QARequest(query=query, candidate_k=body.candidate_k, chat_history=history, project_id=project_id)


async def run_query(
    request: Request,
    principal: Principal,
    body: QaQueryRequest,
    qa_request: QARequest,
    transform: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda e: e,
):
    """Shared by v1 and the legacy query routes."""
    request_id = request.state.request_id

    async def events() -> AsyncIterator[Dict[str, Any]]:
        request_id_var.set(request_id)
        if body.conversation_id is not None:
            await ConversationService.add_message(body.conversation_id, "user", qa_request.query, user_id=principal.user_id)
        final: Optional[Dict[str, Any]] = None
        async for event in answer_stream(principal, qa_request):
            if event["type"] == "done":
                final = event
            yield event
        if body.conversation_id is not None and final is not None:
            await ConversationService.add_message(
                body.conversation_id, "assistant", final["answer"], final.get("citations"), user_id=principal.user_id
            )

    if body.stream:
        async def stream():
            async for event in events():
                yield _sse(transform(event))

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": request_id},
        )

    evidence, final, error = [], None, None
    async for event in events():
        if event["type"] == "citation":
            evidence.append(event)
        elif event["type"] == "done":
            final = event
        elif event["type"] == "error":
            error = event["message"]
    if error or final is None:
        raise HTTPException(status_code=503, detail=error or "No answer produced")
    return {**{k: v for k, v in final.items() if k != "type"}, "evidence": evidence}


@router.post("/qa/query")
async def qa_query(request: Request, body: QaQueryRequest, principal: Principal = Depends(get_current_principal)):
    enforce_qa_limits(request, principal)
    qa_request = await prepare_request(principal, body)
    return await run_query(request, principal, body, qa_request)


@router.get("/metrics")
async def get_metrics(principal: Principal = Depends(get_current_principal)):
    """Per-instance RAG health: stage latencies (p50/p95/p99), cache hit rate,
    fallbacks, citation validation failures, abstentions, provider errors."""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return metrics.snapshot()
