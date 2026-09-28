"""Citation provenance validation.

Every [S#] marker in the answer must resolve to evidence that was in the
authorized retrieval result set for this request; anything else is removed from
the returned answer and reported. This checks provenance only — whether the cited
chunk semantically supports the claim is measured separately (online judge and
offline faithfulness evaluation).
"""

import re
from dataclasses import dataclass, field
from typing import List

from backend.authz.policy import AuthorizationScope
from backend.qa.prompt import Evidence

# ASCII [S1] plus the full-width 【S1】 / ［S1］ brackets some models emit; valid markers
# are normalised to [S1] in the returned answer.
_GROUP_RE = re.compile(
    r"[\[【［]\s*(?:(?:sources?|cited\s+from|see)\s*:?\s*)?(S\d+(?:\s*(?:[,;，、]|and)\s*S\d+)*)\s*[\]】］]",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s*(?:[,;，、]|\band\b)\s*", re.IGNORECASE)
_ZERO_WIDTH_RE = re.compile("[" + "".join(map(chr, (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))) + "]")  # ZWSP, ZWNJ, ZWJ, WJ, BOM
_ABSTAIN_RE = re.compile(r"^\s*i\s+don['’]?t\s+know", re.IGNORECASE)


@dataclass
class CitationReport:
    answer: str
    cited: List[Evidence] = field(default_factory=list)
    invalid_labels: List[str] = field(default_factory=list)
    abstained: bool = False

    @property
    def uncited(self) -> bool:
        """A substantive answer with no valid citation."""
        return not self.abstained and not self.cited

    @property
    def grounded(self) -> bool:
        return self.abstained or (bool(self.cited) and not self.invalid_labels)


def validate_citations(answer: str, evidence: List[Evidence], scope: AuthorizationScope) -> CitationReport:
    # Models sometimes emit zero-width characters inside markers (e.g. a ZWSP after "["), which
    # would otherwise hide a valid citation from the parser.
    answer = _ZERO_WIDTH_RE.sub("", answer)
    by_label = {ev.label.upper(): ev for ev in evidence}
    cited: List[Evidence] = []
    invalid: List[str] = []

    def replace(match: re.Match) -> str:
        kept = []
        for raw in _SPLIT_RE.split(match.group(1)):
            label = raw.upper()
            ev = by_label.get(label)
            if ev and scope.allows(scope.tenant_id, ev.document_id, ev.document_version):
                kept.append(label)
                if ev not in cited:
                    cited.append(ev)
            elif label not in invalid:
                invalid.append(label)
        return "".join(f"[{label}]" for label in kept)

    sanitized = _GROUP_RE.sub(replace, answer)
    sanitized = re.sub(r"[ \t]+([.,;:])", r"\1", sanitized)  # tidy spaces left by removed markers
    return CitationReport(
        answer=sanitized,
        cited=cited,
        invalid_labels=invalid,
        abstained=bool(_ABSTAIN_RE.match(answer)),
    )
