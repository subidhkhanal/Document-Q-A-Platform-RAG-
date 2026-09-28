"""Section -> chunk conversion with provenance (page, section, hashes)."""

import hashlib
from typing import Any, Dict, List

from backend.documents.repository import chunk_id_for


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_chunks(
    sections: List[Dict[str, Any]],
    chunker,
    *,
    tenant_id: int,
    document_id: int,
    version: int,
    embedding_model_version: str,
) -> List[Dict[str, Any]]:
    """Chunk each section separately (so a chunk never spans pages or headings) and
    number chunks globally within the document version."""
    docs = [
        {
            "text": s["text"],
            "page": s.get("page"),
            "section_title": s.get("section_title"),
            "heading_hierarchy": s.get("heading_hierarchy"),
        }
        for s in sections
        if s.get("text", "").strip()
    ]
    raw_chunks = chunker.chunk_documents(docs)

    chunks = []
    for index, c in enumerate(c for c in raw_chunks if c["text"].strip()):
        text = c["text"].strip()
        content_hash = "sha256:" + sha256_hex(text.encode("utf-8"))
        chunks.append({
            "chunk_id": chunk_id_for(document_id, version, index, content_hash),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "document_version": version,
            "chunk_index": index,
            "page_number": c.get("page"),
            "section_title": c.get("section_title"),
            "text": text,
            "token_count": c.get("token_count"),
            "content_hash": content_hash,
            "embedding_model_version": embedding_model_version,
        })
    return chunks
