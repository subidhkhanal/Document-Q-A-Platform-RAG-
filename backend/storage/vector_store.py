"""Pinecone dense index — rebuildable derived state.

Isolation layers:
  * one namespace per tenant (partitioning; a query can only ever touch one tenant)
  * metadata filter on the caller's authorized (document, active version) keys and
    the active embedding model version (filter-aware ANN)
Chunk text lives in PostgreSQL; the index stores ids, scores and filter metadata.
"""

from typing import Any, Dict, List, Optional, Tuple

from backend.config import (
    API_TIMEOUT, COHERE_API_KEY, COHERE_EMBED_DIMENSION, COHERE_EMBED_MODEL, EMBED_BATCH_SIZE,
    PINECONE_API_KEY, PINECONE_CLOUD, PINECONE_INDEX_NAME, PINECONE_REGION,
)

PINECONE_UPSERT_BATCH_SIZE = 100
PINECONE_DELETE_BATCH_SIZE = 1000


def tenant_namespace(tenant_id: int) -> str:
    return f"tenant-{tenant_id}"


class VectorStore:
    def __init__(self):
        if not PINECONE_API_KEY:
            raise ValueError("PINECONE_API_KEY is required. Get a free API key at https://app.pinecone.io")
        if not COHERE_API_KEY:
            raise ValueError("COHERE_API_KEY is required for embeddings. Get a free API key at https://dashboard.cohere.com/api-keys")

        # Imported here, not at module load: keeps serverless cold starts short.
        import cohere
        from pinecone import Pinecone, ServerlessSpec

        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.cohere = cohere.ClientV2(api_key=COHERE_API_KEY, timeout=API_TIMEOUT)

        if PINECONE_INDEX_NAME not in self.pc.list_indexes().names():
            try:
                self.pc.create_index(
                    name=PINECONE_INDEX_NAME,
                    dimension=COHERE_EMBED_DIMENSION,
                    metric="cosine",
                    spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
                )
            except Exception:
                # Another instance may have created it concurrently (409); anything else is real.
                if PINECONE_INDEX_NAME not in self.pc.list_indexes().names():
                    raise
        self.index = self.pc.Index(PINECONE_INDEX_NAME)

    # -- embeddings ---------------------------------------------------------
    def _embed(self, texts: List[str], input_type: str) -> List[List[float]]:
        resp = self.cohere.embed(
            texts=texts, model=COHERE_EMBED_MODEL, input_type=input_type, embedding_types=["float"]
        )
        return [list(v) for v in resp.embeddings.float_]

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text], "search_query")[0]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            vectors.extend(self._embed(texts[i:i + EMBED_BATCH_SIZE], "search_document"))
        return vectors

    # -- writes -------------------------------------------------------------
    def upsert(self, tenant_id: int, vectors: List[Dict[str, Any]]) -> None:
        """Upsert {id, values, metadata}. Chunk ids are deterministic, so retries overwrite."""
        ns = tenant_namespace(tenant_id)
        for i in range(0, len(vectors), PINECONE_UPSERT_BATCH_SIZE):
            self.index.upsert(vectors=vectors[i:i + PINECONE_UPSERT_BATCH_SIZE], namespace=ns)

    def delete_ids(self, tenant_id: int, ids: List[str]) -> None:
        ns = tenant_namespace(tenant_id)
        for i in range(0, len(ids), PINECONE_DELETE_BATCH_SIZE):
            self.index.delete(ids=ids[i:i + PINECONE_DELETE_BATCH_SIZE], namespace=ns)

    # -- reads --------------------------------------------------------------
    def query(
        self,
        tenant_id: int,
        vector: List[float],
        top_k: int,
        metadata_filter: Optional[Dict[str, Any]],
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        results = self.index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
            namespace=tenant_namespace(tenant_id),
        )
        return [(m.id, float(m.score), dict(m.metadata or {})) for m in results.matches]

    def count(self) -> int:
        return self.index.describe_index_stats().total_vector_count


def build_chunk_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Filter metadata attached to every vector."""
    meta = {
        "tenant_id": int(chunk["tenant_id"]),
        "document_id": int(chunk["document_id"]),
        "document_version": int(chunk["document_version"]),
        "doc_version_key": f"{chunk['document_id']}:{chunk['document_version']}",
        "chunk_index": int(chunk["chunk_index"]),
        "embedding_model_version": chunk["embedding_model_version"],
        "content_hash": chunk["content_hash"],
    }
    if chunk.get("page_number") is not None:
        meta["page_number"] = int(chunk["page_number"])
    return meta
