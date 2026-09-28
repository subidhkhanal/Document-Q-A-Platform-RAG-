"""Adapters that add pre-v1 field names to v1 events for older frontends."""

from typing import Any, Dict


def legacy_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Add the old field names (token.content, done.sources) to v1 events."""
    if event["type"] == "token":
        return {**event, "content": event["text"]}
    if event["type"] == "error":
        return {**event, "content": event["message"]}
    if event["type"] == "done":
        sources = [
            {"source": c["source_name"], "page": c["page_number"], "chunk_id": c["chunk_id"], "similarity": 0}
            for c in event.get("citations", [])
        ]
        return {**event, "sources": sources, "chunks_used": len(sources)}
    return event


def build_v1_request(**fields: Any):
    """Construct a v1 QaQueryRequest from legacy fields, mapping validation errors to 422."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    from backend.api.v1.qa import QaQueryRequest

    try:
        return QaQueryRequest(**fields)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False))
