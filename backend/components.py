"""Lazily-initialised shared clients. External API clients are created on first use
so the server starts immediately and a missing key only fails the requests that
need it."""

import threading
import time
from typing import Optional

from backend.config import CHUNKING_METHOD

_vector_store = None
_reranker = None
_llm = None
_router = None
_chunker = None


class ComponentUnavailable(RuntimeError):
    pass


_vector_store_failed_at = 0.0
_VECTOR_STORE_RETRY_SECONDS = 30.0
_vector_store_lock = threading.Lock()


def get_vector_store():
    """Blocking (Pinecone setup makes network calls): call via asyncio.to_thread from
    async code. Failures are cached briefly so an outage fails fast instead of
    re-blocking on every request."""
    global _vector_store, _vector_store_failed_at
    if _vector_store is not None:
        return _vector_store
    with _vector_store_lock:  # concurrent callers run in worker threads
        if _vector_store is None:
            if time.monotonic() - _vector_store_failed_at < _VECTOR_STORE_RETRY_SECONDS:
                raise ComponentUnavailable("Vector store unavailable (recent initialization failure)")
            from backend.storage.vector_store import VectorStore

            try:
                _vector_store = VectorStore()
            except Exception as e:
                _vector_store_failed_at = time.monotonic()
                raise ComponentUnavailable(f"Vector store initialization failed: {e}") from e
    return _vector_store


def get_reranker():
    global _reranker
    if _reranker is None:
        from backend.retrieval.reranker import Reranker

        _reranker = Reranker()
    return _reranker


def get_llm():
    global _llm
    if _llm is None:
        from backend.llm.reasoning import LLMClient

        _llm = LLMClient()
    return _llm


def get_router() -> Optional[object]:
    global _router
    if _router is None:
        from backend.routing.query_router import QueryRouter

        _router = QueryRouter()
    return _router


def get_chunker():
    global _chunker
    if _chunker is None:
        from backend.ingestion.chunker import Chunker
        from backend.ingestion.recursive_chunker import RecursiveChunker

        _chunker = RecursiveChunker() if CHUNKING_METHOD == "recursive" else Chunker()
    return _chunker
