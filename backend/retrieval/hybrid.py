"""Hybrid retrieval with authorization enforced at the retrieval boundary.

  1. Dense ANN (Pinecone, tenant namespace, filtered to the caller's authorized
     document versions + active embedding model)   ─┐ in parallel
  2. Keyword search (PostgreSQL full-text, same authorized version set) ─┘
  3. Reciprocal Rank Fusion
  4. Hydrate chunk text from PostgreSQL and revalidate every candidate against the
     authorization scope (defense in depth — nothing unauthorized reaches a prompt)
  5. Cross-encoder rerank to the final top-k

If the vector database is unavailable, keyword-only retrieval still enforces the
same tenant/ACL scope. If both paths fail, retrieval fails; authorization is never
bypassed to preserve availability.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.authz.policy import AuthorizationScope, version_key
from backend.common.metrics import metrics
from backend.common.retry import retry_async
from backend.components import get_reranker, get_vector_store
from backend.config import (
    DENSE_FILTER_MAX_KEYS, EMBEDDING_MODEL_VERSION, RERANK_CANDIDATES, RERANK_MODEL, RETRIEVAL_CACHE_MAX_ENTRIES,
    RETRIEVAL_CACHE_TTL_SECONDS, RETRIEVAL_TIMEOUT_SECONDS, RRF_K, USE_HYBRID_RETRIEVAL,
)
from backend.db.connection import db_session
from backend.retrieval.cache import TTLCache, cache_key, normalize_query

logger = logging.getLogger(__name__)

_embedding_cache = TTLCache(max_entries=2000, ttl_seconds=3600)
_retrieval_cache = TTLCache(max_entries=RETRIEVAL_CACHE_MAX_ENTRIES, ttl_seconds=RETRIEVAL_CACHE_TTL_SECONDS)


class RetrievalUnavailable(RuntimeError):
    pass


@dataclass
class RetrievalResult:
    chunks: List[Dict[str, Any]]
    candidates: int = 0
    dense_ok: bool = True
    keyword_ok: bool = True
    reranked: bool = False
    cache_hit: bool = False
    timings_ms: Dict[str, float] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return not (self.dense_ok and self.keyword_ok)


def invalidate_tenant_cache(tenant_id: int) -> None:
    _retrieval_cache.invalidate_tag(tenant_id)


def reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = RRF_K) -> List[Tuple[str, float]]:
    """score(d) = Σ 1 / (k + rank_i(d)), ranks starting at 1."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def dense_filter(scope: AuthorizationScope) -> Tuple[Dict[str, Any], bool]:
    """Pinecone metadata filter built only from the server-resolved scope.
    Returns (filter, filtered_by_keys)."""
    base = [
        {"tenant_id": {"$eq": scope.tenant_id}},
        {"embedding_model_version": {"$eq": EMBEDDING_MODEL_VERSION}},
    ]
    keys = scope.version_keys()
    if len(keys) <= DENSE_FILTER_MAX_KEYS:
        return {"$and": base + [{"doc_version_key": {"$in": keys}}]}, True
    return {"$and": base}, False


class HybridRetriever:
    async def embed_query(self, query: str) -> List[float]:
        """Query embedding (cached). Public so callers can start it early, in parallel
        with authorization resolution: it depends only on the query text."""
        key = (EMBEDDING_MODEL_VERSION, normalize_query(query))
        cached = _embedding_cache.get(key)
        if cached is not None:
            return cached
        vs = await asyncio.to_thread(get_vector_store)
        vector = await asyncio.to_thread(vs.embed_query, query)
        _embedding_cache.set(key, vector)
        return vector

    async def _dense(self, query: str, scope: AuthorizationScope, k: int, timings: Dict[str, float],
                     query_vector: Optional["asyncio.Future[List[float]]"] = None) -> List[str]:
        start = time.perf_counter()
        deadline = time.monotonic() + RETRIEVAL_TIMEOUT_SECONDS
        flt, by_keys = dense_filter(scope)
        top_k = k if by_keys else min(k * 3, 1000)  # over-fetch when relying on post-filtering
        prefetched = [query_vector]

        async def attempt():
            embed_start = time.perf_counter()
            task, prefetched[0] = prefetched[0], None  # a failed prefetch is not reused on retry
            vector = await (asyncio.shield(task) if task is not None else self.embed_query(query))
            timings["embed_wait_ms"] = _ms(embed_start)
            query_start = time.perf_counter()
            vs = await asyncio.to_thread(get_vector_store)
            matches = await asyncio.to_thread(vs.query, scope.tenant_id, vector, top_k, flt)
            timings["dense_query_ms"] = _ms(query_start)
            return matches

        matches = await asyncio.wait_for(
            retry_async(attempt, attempts=2, base_delay=0.1, deadline=deadline, label="dense_retrieval"),
            timeout=RETRIEVAL_TIMEOUT_SECONDS,
        )
        allowed = set(scope.version_keys())
        ids = [
            chunk_id for chunk_id, _, meta in matches
            if meta.get("doc_version_key") in allowed and int(meta.get("tenant_id", -1)) == scope.tenant_id
        ][:k]
        timings["dense_ms"] = _ms(start)
        metrics.observe_ms("retrieval.dense", timings["dense_ms"])
        return ids

    async def _keyword(self, query: str, scope: AuthorizationScope, k: int, timings: Dict[str, float]) -> List[str]:
        start = time.perf_counter()
        doc_ids = [d.document_id for d in scope.docs.values()]
        versions = [d.version for d in scope.docs.values()]
        async with db_session() as db:
            rows = await asyncio.wait_for(
                db.fetch_all(
                    """SELECT c.chunk_id, ts_rank_cd(c.tsv, q) AS score
                       FROM chunks c,
                            to_tsquery('simple', replace(plainto_tsquery('english', $1)::text, ' & ', ' | ')) q
                       WHERE c.tenant_id = $2
                         AND (c.document_id, c.document_version) IN (
                             SELECT * FROM unnest($3::int[], $4::int[]))
                         AND c.tsv @@ q
                       ORDER BY score DESC
                       LIMIT $5""",
                    query, scope.tenant_id, doc_ids, versions, k,
                ),
                timeout=RETRIEVAL_TIMEOUT_SECONDS,
            )
        timings["keyword_ms"] = _ms(start)
        metrics.observe_ms("retrieval.keyword", timings["keyword_ms"])
        return [r["chunk_id"] for r in rows]

    async def _hydrate(self, chunk_ids: List[str], scope: AuthorizationScope) -> Dict[str, Dict[str, Any]]:
        """Load chunk text + provenance and drop anything outside the scope."""
        if not chunk_ids:
            return {}
        async with db_session() as db:
            rows = await db.fetch_all(
                """SELECT c.chunk_id, c.tenant_id, c.document_id, c.document_version, c.chunk_index,
                          c.page_number, c.section_title, c.text, c.content_hash, v.filename
                   FROM chunks c
                   JOIN document_versions v ON v.document_id = c.document_id AND v.version = c.document_version
                   WHERE c.chunk_id = ANY($1::text[]) AND c.tenant_id = $2""",
                chunk_ids, scope.tenant_id,
            )
        hydrated = {}
        for r in rows:
            if scope.allows(r["tenant_id"], r["document_id"], r["document_version"]):
                hydrated[r["chunk_id"]] = r
            else:
                metrics.incr("retrieval.revalidation_dropped")
                logger.warning("Dropped chunk %s outside authorization scope", r["chunk_id"])
        return hydrated

    async def retrieve(
        self,
        query: str,
        scope: AuthorizationScope,
        *,
        candidate_k: int,
        top_k: int,
        use_rerank: bool = True,
        query_vector: Optional["asyncio.Future[List[float]]"] = None,
    ) -> RetrievalResult:
        if scope.is_empty:
            metrics.incr("retrieval.empty_scope")
            return RetrievalResult(chunks=[])

        reranker = get_reranker()
        rerank = use_rerank and reranker.is_available()
        key = cache_key(
            tenant=scope.tenant_id, scope=scope.fingerprint, q=normalize_query(query), candidate_k=candidate_k,
            top_k=top_k, rerank=rerank, rerank_model=RERANK_MODEL, rerank_candidates=RERANK_CANDIDATES,
            hybrid=USE_HYBRID_RETRIEVAL, embedding=EMBEDDING_MODEL_VERSION,
        )
        cached = _retrieval_cache.get(key)
        if cached is not None:
            metrics.incr("retrieval.cache_hit")
            return RetrievalResult(chunks=[dict(c) for c in cached.chunks], candidates=cached.candidates,
                                   reranked=cached.reranked, cache_hit=True)
        metrics.incr("retrieval.cache_miss")

        start = time.perf_counter()
        timings: Dict[str, float] = {}
        # Dense (embed -> ANN) and keyword search run concurrently.
        tasks = [self._dense(query, scope, candidate_k, timings, query_vector)]
        if USE_HYBRID_RETRIEVAL:
            tasks.append(self._keyword(query, scope, candidate_k, timings))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        dense_ids: List[str] = []
        keyword_ids: List[str] = []
        dense_ok = keyword_ok = True
        if isinstance(results[0], BaseException):
            dense_ok = False
            metrics.incr("retrieval.dense_failed")
            logger.warning("Dense retrieval failed: %r", results[0])
        else:
            dense_ids = results[0]
        if USE_HYBRID_RETRIEVAL:
            if isinstance(results[1], BaseException):
                keyword_ok = False
                metrics.incr("retrieval.keyword_failed")
                logger.warning("Keyword retrieval failed: %r", results[1])
            else:
                keyword_ids = results[1]
        if not dense_ok and not (USE_HYBRID_RETRIEVAL and keyword_ok):
            raise RetrievalUnavailable("Retrieval backends are unavailable")
        if not dense_ok:
            metrics.incr("retrieval.keyword_only_fallback")
        timings["recall_ms"] = _ms(start)

        fused = reciprocal_rank_fusion([l for l in (dense_ids, keyword_ids) if l])[:candidate_k]
        hydrate_start = time.perf_counter()
        hydrated = await self._hydrate([cid for cid, _ in fused], scope)
        timings["hydrate_ms"] = _ms(hydrate_start)
        candidates = []
        for cid, score in fused:
            if cid in hydrated:
                chunk = dict(hydrated[cid])
                chunk["rrf_score"] = score
                chunk["retrieved_by"] = [n for n, ids in (("dense", dense_ids), ("keyword", keyword_ids)) if cid in ids]
                candidates.append(chunk)

        final = candidates[:top_k]
        reranked = False
        if rerank and candidates:
            rerank_start = time.perf_counter()
            try:
                # Cross-encoder cost grows with candidate count; only the fused head is rescored.
                final = await asyncio.to_thread(reranker.rerank, query, candidates[:RERANK_CANDIDATES], top_k)
                reranked = True
            except Exception as e:
                metrics.incr("rerank.failed")
                logger.warning("Rerank failed, using fused order: %r", e)
            timings["rerank_ms"] = _ms(rerank_start)
            metrics.observe_ms("retrieval.rerank", timings["rerank_ms"])

        timings["retrieval_ms"] = _ms(start)
        metrics.observe_ms("retrieval.total", timings["retrieval_ms"])
        if not final:
            metrics.incr("retrieval.empty_result")

        result = RetrievalResult(
            chunks=final, candidates=len(candidates), dense_ok=dense_ok, keyword_ok=keyword_ok,
            reranked=reranked, timings_ms=timings,
        )
        if dense_ok and keyword_ok:
            _retrieval_cache.set(key, result, tag=scope.tenant_id)  # never cache degraded results
        return result


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


retriever = HybridRetriever()


def chunk_version_key(chunk: Dict[str, Any]) -> str:
    return version_key(chunk["document_id"], chunk["document_version"])
