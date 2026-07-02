from __future__ import annotations

from dataclasses import dataclass

SHIPPED_PATTERNS: tuple[str, ...] = ("shipped", "cherry-picked", "merged-via", "salvaged into")
DUPLICATE_PATTERNS: tuple[str, ...] = ("duplicate",)
SUPERSEDED_PATTERNS: tuple[str, ...] = ("supersede", "consolidat", "closing in favor", "closed in favor")
CREDIT_PATTERNS: tuple[str, ...] = ("co-author", "coauthor", "co-authored", "authorship", "attribution", "credited")
CONTINUATION_PATTERNS: tuple[str, ...] = ("same credit", "same commit", "same change", "reopen")


@dataclass(frozen=True)
class ClassificationResult:
    classification: str
    release: str = ""
    via_label: str = ""
    via_url: str = ""
    evidence_kind: str = "lost"
    from_cache: bool = False
    log_label: str = "lost"

