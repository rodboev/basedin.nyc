from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from rich.console import Console

from core.classification_rebuild import CacheDivergence, CacheRebuildInterrupted, classification_entry_matches_result, rebuild_classification_cache, split_classification_cache_key, write_divergence_report
from core.classify import ClassificationResult
from core.models import Cache, ClassificationEntry, Evidence, PullRequest, UserRef


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


def test_rebuild_classification_cache_uses_ps1_progress_shape(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    buffer = StringIO()
    source_cache = Cache(
        entries={
            "owner/repo#7": ClassificationEntry(
                classification="shipped",
                evidenceKind="direct-merge",
                cachedAt="2026-07-02T00:00:00Z",
            )
        }
    )
    repos_file = tmp_path / "generate.ps1"
    repos_file.write_text("owner/repo", encoding="utf-8")

    monkeypatch.setattr("core.classification_rebuild.CONSOLE", Console(file=buffer, color_system=None, force_terminal=False, highlight=False))
    monkeypatch.setattr("core.classification_rebuild.load_cache", lambda _path: source_cache)
    monkeypatch.setattr("core.classification_rebuild.save_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.classification_rebuild.load_active_repos_from_text", lambda _text: ["owner/repo"])
    monkeypatch.setattr(
        "core.classification_rebuild.cached_or_live_pull_request",
        lambda _cache, repo, number: PullRequest(repo=repo, number=number, author=UserRef(login="rodboev")),
    )
    monkeypatch.setattr("core.classification_rebuild.live_evidence", lambda _repo, _number, _pr: Evidence())
    monkeypatch.setattr(
        "core.classification_rebuild.classify_closed_pr",
        lambda _pr, _evidence: ClassificationResult(
            classification="shipped",
            evidence_kind="direct-merge",
            log_label="shipped (merged directly)",
        ),
    )

    result = rebuild_classification_cache(
        cache_file=tmp_path / "input.json",
        out_cache_file=tmp_path / "output.json",
        divergence_file=tmp_path / "divergences.json",
        repos_file=repos_file,
        active_repos_only=False,
        limit=1,
        save_every=0,
    )

    assert result.checked == 1
    assert result.skipped == 0
    assert result.failed == 0
    assert result.divergences == 0
    assert buffer.getvalue() == "Classifying 1 closed PRs...\n  #7 (repo)... shipped (merged directly)\n"


def test_rebuild_classification_cache_saves_checkpoint_on_interrupt(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    buffer = StringIO()
    source_cache = Cache(
        entries={
            "owner/repo#7": ClassificationEntry(classification="shipped", evidenceKind="direct-merge", cachedAt="2026-07-02T00:00:00Z"),
            "owner/repo#8": ClassificationEntry(classification="lost", evidenceKind="lost", cachedAt="2026-07-02T00:00:00Z"),
        }
    )
    repos_file = tmp_path / "generate.ps1"
    out_cache_file = tmp_path / "output.json"
    divergence_file = tmp_path / "divergences.json"
    repos_file.write_text("owner/repo", encoding="utf-8")

    call_count = 0

    def fake_classify(_pr: PullRequest, _evidence: Evidence) -> ClassificationResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ClassificationResult(classification="shipped", evidence_kind="direct-merge", log_label="shipped (merged directly)")
        raise KeyboardInterrupt

    monkeypatch.setattr("core.classification_rebuild.CONSOLE", Console(file=buffer, color_system=None, force_terminal=False, highlight=False))
    monkeypatch.setattr("core.classification_rebuild.load_cache", lambda _path: source_cache)
    monkeypatch.setattr("core.classification_rebuild.load_active_repos_from_text", lambda _text: ["owner/repo"])
    monkeypatch.setattr(
        "core.classification_rebuild.cached_or_live_pull_request",
        lambda _cache, repo, number: PullRequest(repo=repo, number=number, author=UserRef(login="rodboev")),
    )
    monkeypatch.setattr("core.classification_rebuild.live_evidence", lambda _repo, _number, _pr: Evidence())
    monkeypatch.setattr("core.classification_rebuild.classify_closed_pr", fake_classify)

    with pytest.raises(CacheRebuildInterrupted) as excinfo:
        rebuild_classification_cache(
            cache_file=tmp_path / "input.json",
            out_cache_file=out_cache_file,
            divergence_file=divergence_file,
            repos_file=repos_file,
            active_repos_only=False,
            save_every=25,
        )

    assert excinfo.value.result.checked == 1
    assert out_cache_file.exists()
    assert divergence_file.exists()
    payload = json.loads(divergence_file.read_text(encoding="utf-8"))
    assert payload == []
    saved_cache = Cache.model_validate_json(out_cache_file.read_text(encoding="utf-8"))
    assert saved_cache.entries["owner/repo#7"].cachedAt != source_cache.entries["owner/repo#7"].cachedAt
    assert saved_cache.entries["owner/repo#8"].cachedAt == source_cache.entries["owner/repo#8"].cachedAt
    assert "Interrupted after classifying 1 PRs, skipped 0, failed 0, divergences 0." in buffer.getvalue()
    assert "Checkpoint saved." in buffer.getvalue()
