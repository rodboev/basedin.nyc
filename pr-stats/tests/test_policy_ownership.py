from __future__ import annotations

from pathlib import Path

from core import classify


def test_classification_patterns_are_owned_by_classify(repo_root: Path) -> None:
    forbidden = {
        "MAINTAINER_SHIP_PATTERN",
        "DUPLICATE_PATTERNS",
        "SUPERSEDED_PATTERNS",
        "CREDIT_PATTERNS",
        "CONTINUATION_PATTERNS",
    }
    forbidden.update(
        (
            classify.MAINTAINER_SHIP_PATTERN.pattern,
            classify.WITHDRAWN_PATTERN.pattern,
            classify.AUTHOR_CLOSE_PATTERN.pattern,
            classify.MERGED_CARRY_FORWARD_PATTERN.pattern,
            classify.NEGATIVE_REFERENCE_PATTERN.pattern,
            classify.POSITIVE_REFERENCE_PATTERN.pattern,
        ),
    )

    offenders: list[str] = []
    for path in (repo_root / "core").glob("*.py"):
        if path.name == "classify.py":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern and pattern in text:
                offenders.append(f"{path.name}: {pattern}")

    assert offenders == []
