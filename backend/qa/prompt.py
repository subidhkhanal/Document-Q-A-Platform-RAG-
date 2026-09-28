"""Prompt construction. Retrieved text is placed inside a delimited context block
with explicit provenance headers and is neutralised so it cannot close the block
or impersonate source labels."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.routing.query_router import RouteType

_CONTEXT_TAG_RE = re.compile(r"</?\s*retrieved_context\s*>", re.IGNORECASE)
_LABEL_RE = re.compile(r"\[(S\d+)\]")

ROUTE_STYLE = {
    RouteType.SUMMARY: "Write a structured summary: a one-sentence overview, key points as bullets, then conclusions. Cite every point.",
    RouteType.COMPARISON: "Structure the answer as similarities, differences and a short conclusion. Say explicitly if evidence for one side is missing. Cite every point.",
}


@dataclass(frozen=True)
class Evidence:
    label: str
    chunk_id: str
    document_id: int
    document_version: int
    page_number: Optional[int]
    source_name: str
    section_title: Optional[str]
    text: str
    score: Optional[float]

    def citation(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "chunk_id": self.chunk_id,
            "page_number": self.page_number,
            "source_name": self.source_name,
            "section_title": self.section_title,
            "text": self.text,  # the caller is authorized for this passage
        }


def build_evidence(chunks: List[Dict[str, Any]]) -> List[Evidence]:
    return [
        Evidence(
            label=f"S{i}",
            chunk_id=c["chunk_id"],
            document_id=c["document_id"],
            document_version=c["document_version"],
            page_number=c.get("page_number"),
            source_name=c["filename"],
            section_title=c.get("section_title"),
            text=c["text"],
            score=c.get("rerank_score", c.get("rrf_score")),
        )
        for i, c in enumerate(chunks, start=1)
    ]


def sanitize_evidence_text(text: str) -> str:
    text = _CONTEXT_TAG_RE.sub("[tag removed]", text)
    return _LABEL_RE.sub(r"(\1)", text)


def format_context(evidence: List[Evidence]) -> str:
    blocks = []
    for ev in evidence:
        header = f"[{ev.label}] Document: {ev.source_name} | Version: {ev.document_version}"
        if ev.page_number is not None:
            header += f" | Page: {ev.page_number}"
        if ev.section_title:
            header += f" | Section: {ev.section_title}"
        header += f" | Chunk: {ev.chunk_id}"
        blocks.append(f"{header}\n{sanitize_evidence_text(ev.text)}")
    return "<retrieved_context>\n" + "\n\n".join(blocks) + "\n</retrieved_context>"


def build_user_message(query: str, evidence: List[Evidence], route_type: Optional[RouteType] = None) -> str:
    parts = [format_context(evidence), f"User Query: {query}"]
    style = ROUTE_STYLE.get(route_type)
    if style:
        parts.append(style)
    parts.append(
        "Answer only from the evidence above. Put the source label right after each claim it supports, "
        'for example: "Employees receive 20 days of PTO [S1]."'
    )
    return "\n\n".join(parts)
