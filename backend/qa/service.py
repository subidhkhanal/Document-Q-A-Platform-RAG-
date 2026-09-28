"""RAG orchestrator: retrieve first, generate second.

Event stream (SSE payloads):
  {"type": "status",   "stage": ...}
  {"type": "citation", "label", "document_id", "document_version", "chunk_id",
                       "page_number", "source_name", "section_title", "text"}   # authorized evidence
  {"type": "token",    "text": ...}
  {"type": "done",     "answer", "citations", "invalid_citations", "grounded",
                       "abstained", "route_type", "degraded", "timings", "request_id"}
  {"type": "error",    "message": ...}

Authorization is resolved from the policy store before retrieval and fails
closed; the model only ever sees evidence from the caller's authorized scope.
"""

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

from backend.audit.log import record_event
from backend.auth.principal import Principal
from backend.authz.policy import resolve_scope
from backend.common.metrics import metrics
from backend.common.request_context import current_request_id
from backend.components import get_llm, get_router
from backend.config import (
    ABSTAIN_ANSWER, DEFAULT_CANDIDATE_K, EMBEDDING_MODEL_VERSION, ENABLE_QUERY_ROUTING, LLM_PROVIDER,
    ROUTER_LLM_CLASSIFICATION,
    MIN_RERANK_SCORE, ONLINE_JUDGE_SAMPLE_RATE, RERANK_MODEL, RERANK_TOP_K, SYSTEM_PROMPT,
)
from backend.documents.repository import list_readable_documents
from backend.llm.reasoning import LLMUnavailable
from backend.qa.citations import validate_citations
from backend.qa.judge import judge_faithfulness
from backend.qa.prompt import build_evidence, build_user_message
from backend.retrieval.hybrid import RetrievalUnavailable, retriever
from backend.routing.query_router import RouteResult, RouteType

logger = logging.getLogger(__name__)

GREETING_ANSWER = "Hello! Ask me a question about the documents you have access to and I'll answer with citations."
CLARIFY_ANSWER = (
    "Could you be more specific? For example: \"What does the HR manual say about PTO rollover?\" "
    "A specific question lets me find the right passages."
)
SUMMARY_TOP_K = max(RERANK_TOP_K, 8)
_background_tasks: set = set()  # strong refs so sampled judge tasks are not garbage-collected


@dataclass
class QARequest:
    query: str
    candidate_k: int = DEFAULT_CANDIDATE_K
    chat_history: Optional[List[Dict[str, str]]] = None
    project_id: Optional[int] = None


def _done(**fields: Any) -> Dict[str, Any]:
    return {"type": "done", "request_id": current_request_id(), **fields}


async def _classify(req: QARequest) -> RouteResult:
    if not ENABLE_QUERY_ROUTING:
        return RouteResult(route_type=RouteType.KNOWLEDGE)
    try:
        router = get_router()
        if not req.chat_history and not ROUTER_LLM_CLASSIFICATION:
            # Keyword routing only: saves an LLM round trip on the latency-critical path.
            return router.classify_fast(req.query)
        # The LLM path resolves follow-up references ("what about its deductible?").
        return await router.classify(req.query, chat_history=req.chat_history)
    except Exception:
        logger.exception("Query routing failed; defaulting to KNOWLEDGE")
        return RouteResult(route_type=RouteType.KNOWLEDGE)


async def answer_stream(principal: Principal, req: QARequest) -> AsyncIterator[Dict[str, Any]]:
    started = time.perf_counter()
    request_id = current_request_id()
    metrics.incr("qa.requests")
    audit: Dict[str, Any] = {"query_sha256": hashlib.sha256(req.query.encode()).hexdigest()}

    route = await _classify(req)
    stage_ms: Dict[str, float] = {"route_ms": _ms(started)}
    if route.route_type == RouteType.OUT_OF_SCOPE:
        # The classifier over-refuses legitimate document questions. Let retrieval decide:
        # without supporting evidence the pipeline abstains anyway.
        route = RouteResult(route_type=RouteType.KNOWLEDGE, rewritten_query=route.rewritten_query)
    audit["route"] = route.route_type.value
    effective_query = route.rewritten_query or req.query

    # -- non-retrieval routes ---------------------------------------------------
    canned = {
        RouteType.GREETING: GREETING_ANSWER,
        RouteType.CLARIFICATION: CLARIFY_ANSWER,
    }.get(route.route_type)
    if canned:
        yield {"type": "token", "text": canned}
        yield _done(answer=canned, citations=[], invalid_citations=[], grounded=True, abstained=False,
                    route_type=route.route_type.value, degraded=False, timings={})
        return

    if route.route_type == RouteType.META:
        try:
            docs = await list_readable_documents(principal, req.project_id)
        except Exception:
            logger.exception("Policy store unavailable")
            metrics.incr("qa.authz_unavailable")
            yield {"type": "error", "message": "Authorization service unavailable. Please retry."}
            return
        ready = [d for d in docs if d["active_version"]]
        if ready:
            listing = "\n".join(f"- {d['filename']} (v{d['active_version']})" for d in ready)
            answer = f"You can query {len(ready)} document(s):\n\n{listing}"
        else:
            answer = "You don't have access to any indexed documents yet. Upload a PDF, Word, text or EPUB file to get started."
        yield {"type": "token", "text": answer}
        yield _done(answer=answer, citations=[], invalid_citations=[], grounded=True, abstained=False,
                    route_type=route.route_type.value, degraded=False, timings={})
        return

    # -- authorization (fail closed) ----------------------------------------------
    # The query embedding depends only on the query text, so it is computed while the
    # authorization scope is resolved instead of after it.
    embedding_task = asyncio.create_task(retriever.embed_query(effective_query))
    yield {"type": "status", "stage": "authorizing"}
    authz_start = time.perf_counter()
    try:
        scope = await resolve_scope(principal, req.project_id)
    except Exception:
        embedding_task.cancel()
        logger.exception("Policy store unavailable")
        metrics.incr("qa.authz_unavailable")
        yield {"type": "error", "message": "Authorization service unavailable. Please retry."}
        return
    stage_ms["authz_ms"] = _ms(authz_start)
    audit.update(scope_fingerprint=scope.fingerprint, policy_version=scope.policy_version,
                 authorized_documents=len(scope.docs))

    llm = get_llm()
    if LLM_PROVIDER not in principal.allowed_llm_providers:
        embedding_task.cancel()
        metrics.incr("qa.provider_not_allowed")
        yield {"type": "error", "message": "Your organization has not approved the configured LLM provider."}
        return

    # -- retrieval ------------------------------------------------------------------
    yield {"type": "status", "stage": "retrieving"}
    top_k = SUMMARY_TOP_K if route.route_type in (RouteType.SUMMARY, RouteType.COMPARISON) else RERANK_TOP_K
    try:
        result = await retriever.retrieve(effective_query, scope, candidate_k=req.candidate_k, top_k=top_k,
                                          query_vector=embedding_task)
    except Exception as e:
        if not isinstance(e, RetrievalUnavailable):
            logger.exception("Retrieval failed")
        metrics.incr("qa.retrieval_unavailable")
        yield {"type": "error", "message": "Search is temporarily unavailable. Please retry shortly."}
        await _audit(principal, request_id, {**audit, "outcome": "retrieval_unavailable"})
        return

    if not embedding_task.done():
        embedding_task.cancel()  # e.g. empty scope or cache hit: result not needed
    evidence = build_evidence(result.chunks)
    timings = {**stage_ms, **result.timings_ms, "cache_hit": result.cache_hit}
    audit.update(
        retrieved=[{"chunk_id": e.chunk_id, "document_id": e.document_id, "version": e.document_version}
                   for e in evidence],
        reranked=result.reranked, degraded=result.degraded, cache_hit=result.cache_hit,
        embedding_model_version=EMBEDDING_MODEL_VERSION, rerank_model=RERANK_MODEL if result.reranked else None,
    )

    best = max((e.score or 0.0) for e in evidence) if evidence else 0.0
    # Cross-encoder scores are calibrated for single-intent queries; compound summary /
    # comparison questions score low even on relevant passages, so for those the
    # grounded prompt ("I don't know") and citation validation decide instead.
    single_intent = route.route_type not in (RouteType.SUMMARY, RouteType.COMPARISON)
    if not evidence or (single_intent and result.reranked and best < MIN_RERANK_SCORE):
        metrics.incr("qa.abstained_no_evidence")
        yield {"type": "token", "text": ABSTAIN_ANSWER}
        yield _done(answer=ABSTAIN_ANSWER, citations=[], invalid_citations=[], grounded=True, abstained=True,
                    route_type=route.route_type.value, degraded=result.degraded, timings=timings)
        await _audit(principal, request_id, {**audit, "outcome": "abstained_insufficient_evidence"})
        return

    for ev in evidence:
        yield {"type": "citation", **ev.citation()}

    # -- generation -----------------------------------------------------------------
    yield {"type": "status", "stage": "generating"}
    gen_start = time.perf_counter()
    first_token_ms: Optional[float] = None
    parts: List[str] = []
    try:
        async for token in llm.stream(SYSTEM_PROMPT, build_user_message(effective_query, evidence, route.route_type)):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1000
            parts.append(token)
            yield {"type": "token", "text": token}
    except LLMUnavailable as e:
        logger.warning("LLM unavailable: %s", e)
        message = (
            "The demo is getting more questions than its free model quota allows. Please try again in a minute."
            if e.rate_limited else "The answer service is temporarily unavailable. No answer was generated."
        )
        yield {"type": "error", "message": message}
        await _audit(principal, request_id, {**audit, "outcome": "llm_unavailable"})
        return

    report = validate_citations("".join(parts), evidence, scope)
    timings.update(
        llm_ttft_ms=round(first_token_ms - (gen_start - started) * 1000, 1) if first_token_ms else None,
        generation_ms=_ms(gen_start),
        ttft_ms=round(first_token_ms, 1) if first_token_ms else None,
        total_ms=_ms(started),
    )
    logger.info("qa timings %s", {k: v for k, v in timings.items() if v is not None})
    metrics.observe_ms("qa.total", timings["total_ms"])
    if first_token_ms:
        metrics.observe_ms("qa.ttft", first_token_ms)
    if report.invalid_labels:
        metrics.incr("qa.citation_validation_failures")
    if report.uncited:
        metrics.incr("qa.uncited_answers")
    if report.abstained:
        metrics.incr("qa.abstained_by_model")

    yield _done(
        answer=report.answer,
        citations=[ev.citation() for ev in report.cited],
        invalid_citations=report.invalid_labels,
        grounded=report.grounded,
        abstained=report.abstained,
        route_type=route.route_type.value,
        degraded=result.degraded,
        timings=timings,
    )

    await _audit(principal, request_id, {
        **audit,
        "outcome": "answered",
        "cited_chunks": [ev.chunk_id for ev in report.cited],
        "invalid_citations": report.invalid_labels,
        "llm_provider": llm.provider, "llm_model": llm.last_model or llm.model,
        "timings": timings,
    })

    if not report.abstained and ONLINE_JUDGE_SAMPLE_RATE > 0 and random.random() < ONLINE_JUDGE_SAMPLE_RATE:
        task = asyncio.create_task(judge_faithfulness(
            tenant_id=principal.tenant_id, user_id=principal.user_id, request_id=request_id,
            query=effective_query, answer=report.answer, evidence=report.cited or evidence,
        ))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _audit(principal: Principal, request_id: str, details: Dict[str, Any]) -> None:
    await record_event("qa.query", tenant_id=principal.tenant_id, user_id=principal.user_id,
                       request_id=request_id, details=details)


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)
