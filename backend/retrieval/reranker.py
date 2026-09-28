from typing import Any, Dict, List

from backend.config import API_TIMEOUT, COHERE_API_KEY, RERANK_MODEL, RERANK_TOP_K


class Reranker:
    """Cohere cross-encoder reranker. Raises on provider errors so the caller can
    record the degradation and fall back to fused ranking."""

    def __init__(self):
        self.client = None
        if COHERE_API_KEY:
            import cohere  # lazy: keeps cold starts short

            self.client = cohere.ClientV2(api_key=COHERE_API_KEY, timeout=API_TIMEOUT)

    def is_available(self) -> bool:
        return self.client is not None

    def rerank(self, query: str, documents: List[Dict[str, Any]], top_k: int = RERANK_TOP_K) -> List[Dict[str, Any]]:
        """Blocking. Returns the top_k documents with a `rerank_score`."""
        if not documents:
            return []
        resp = self.client.rerank(
            model=RERANK_MODEL,
            query=query,
            documents=[doc["text"] for doc in documents],
            top_n=min(top_k, len(documents)),
        )
        results = []
        for item in resp.results:
            doc = dict(documents[item.index])
            doc["rerank_score"] = float(item.relevance_score)
            results.append(doc)
        return results
