from __future__ import annotations

import json
from pathlib import Path

from core.classification_rebuild import CacheDivergence, classification_entry_matches_result, split_classification_cache_key, write_divergence_report
from core.classify import ClassificationResult
from core.models import ClassificationEntry


def test_classification_entry_matches_result_checks_parity_fields() -> None:
    entry = ClassificationEntry(
        classification="shipped",
        evidenceKind="timeline",
        viaLabel="#2",
        viaUrl="https://github.com/owner/repo/pull/2",
        release="v1.2.3",
    )

    assert classification_entry_matches_result(
        entry,
        ClassificationResult(
            classification="shipped",
            evidence_kind="timeline",
            via_label="#2",
            via_url="https://github.com/owner/repo/pull/2",
            release="v1.2.3",
        ),
    )
    assert not classification_entry_matches_result(entry, ClassificationResult(classification="lost", evidence_kind="lost"))


def test_split_classification_cache_key_handles_repo_with_slash() -> None:
    assert split_classification_cache_key("owner/repo#123") == ("owner/repo", 123)


def test_write_divergence_report_uses_stable_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "divergences.json"

    write_divergence_report(
        [
            CacheDivergence(
                key="owner/repo#1",
                expected=ClassificationEntry(classification="lost", evidenceKind="lost"),
                actual=ClassificationResult(classification="shipped", evidence_kind="direct-merge", via_label="direct"),
            ),
        ],
        path,
    )

    assert json.loads(path.read_text(encoding="utf-8")) == [
        {
            "key": "owner/repo#1",
            "expected": {
                "classification": "lost",
                "evidenceKind": "lost",
                "viaLabel": "",
                "viaUrl": "",
                "release": "",
            },
            "actual": {
                "classification": "shipped",
                "evidenceKind": "direct-merge",
                "viaLabel": "direct",
                "viaUrl": "",
                "release": "",
            },
        },
    ]
