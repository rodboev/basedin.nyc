from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import re

from pytest import CaptureFixture, MonkeyPatch

import generate
from core.classification_rebuild import CacheRebuildInterrupted, CacheRebuildResult
from core.classify import ClassificationResult
from core.models import Evidence
from core.report import EASTERN

def test_verify_webui_credits_only_uses_python_credit_pipeline(repo_root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "generate.py",
            "--verify-webui-cached-credits-only",
            "--cache-file",
            ".pr-classification-cache.json",
            "--changelog-file",
            "tests/fixtures/hermes-webui-changelog-credit-lines.txt",
            "--contributors-file",
            "tests/fixtures/hermes-webui-CONTRIBUTORS.md",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"franksong2702: \d+ \(expected >= 200\) OK", result.stdout)
    assert re.search(r"Michaelyklam: \d+ \(expected >= 100\) OK", result.stdout)
    assert re.search(r"rodboev: \d+ \(expected >= 150\) OK", result.stdout)
    assert re.search(r"ai-ag2026: \d+ \(expected >= 80\) OK", result.stdout)
    assert re.search(r"rodboev/franksong2702: 0\.\d{2} \(expected >= 0\.50\) OK", result.stdout)
    assert re.search(r"Michaelyklam/franksong2702: 0\.\d{2} \(expected >= 0\.35\) OK", result.stdout)
    assert "FAIL" not in result.stdout

def test_verify_webui_credits_only_rejects_stale_live_changelog(
    repo_root: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(generate, "run_gh", lambda *_args: "different-changelog-sha")

    result = generate.verify_webui_credits_only(
        cache_file=repo_root / ".pr-classification-cache.json",
        changelog_file=None,
        contributors_file=repo_root / "tests/fixtures/hermes-webui-CONTRIBUTORS.md",
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "release credit cache for nesquena/hermes-webui is stale" in captured.err
    assert "rebuild is not implemented in Python yet" in captured.err

def test_default_generate_fetches_live_prs_instead_of_reusing_stale_html(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    out_file = tmp_path / "index.html"
    cache_file = tmp_path / "cache.json"
    repos_file = tmp_path / "repos.txt"
    cache_file.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")

    monkeypatch.setattr(
        generate,
        "run_gh",
        lambda *_args, **_kwargs: json.dumps(
            [
                _gh_pr_list_item(
                    number=101,
                    state="MERGED",
                    title="Fresh merge absent from stale HTML",
                    closedAt="2026-07-02T17:00:00Z",
                    mergedAt="2026-07-02T17:00:00Z",
                ),
            ],
        ),
    )

    readme_file = tmp_path / "README.md"
    readme_file.write_text(
        "Representative merged PRs:\n"
        "- [#101](https://github.com/owner/repo/pull/101) — lands a fresh merge\n",
        encoding="utf-8",
    )

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
        readme_file=readme_file,
    )

    assert result == 0
    content = out_file.read_text(encoding="utf-8")
    assert "var PR_DATA = " in content
    assert "var TL_ALL = " in content
    assert "Fresh merge absent from stale HTML" in content
    assert '"number":101' in content
    now_eastern = datetime.now(timezone.utc).astimezone(EASTERN)
    assert f"Generated {now_eastern.strftime('%B')} {now_eastern.day}, {now_eastern.year} from GitHub API" in content
    assert '<td class="rep-desc-cell">lands a fresh merge</td>' in content
    assert re.search(r"\{\{ \w+ \}\}", content) is None

def test_default_generate_sanity_gate_keeps_existing_output(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    in_file = tmp_path / "broken.html"
    out_file = tmp_path / "index.html"
    repos_file = tmp_path / "repos.txt"
    cache_file = tmp_path / "cache.json"
    in_file.write_text("no report data", encoding="utf-8")
    out_file.write_text('<div class="number">30</div><div class="label">Total PRs</div>', encoding="utf-8")
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")
    cache_file.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
    monkeypatch.setattr(
        generate,
        "run_gh",
        lambda *_args, **_kwargs: json.dumps([_gh_pr_list_item(number=101, state="MERGED", mergedAt="2026-07-02T17:00:00Z")]),
    )

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=in_file,
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 1
    assert out_file.read_text(encoding="utf-8") == '<div class="number">30</div><div class="label">Total PRs</div>'

def test_default_generate_classifies_and_caches_closed_unmerged_live_pr(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    out_file = tmp_path / "index.html"
    repos_file = tmp_path / "repos.txt"
    cache_file = tmp_path / "cache.json"
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")
    cache_file.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
    monkeypatch.setattr(
        generate,
        "run_gh",
        lambda *_args, **_kwargs: json.dumps([_gh_pr_list_item(number=7, state="CLOSED", closedAt="2026-07-02T17:00:00Z")]),
    )
    monkeypatch.setattr(generate, "live_evidence", lambda *_args, **_kwargs: Evidence())
    monkeypatch.setattr(
        generate,
        "classify_closed_pr",
        lambda *_args, **_kwargs: ClassificationResult(
            classification="accepted-indirect",
            evidence_kind="accepted-indirect",
            via_label="#9",
            via_url="https://github.com/owner/repo/pull/9",
        ),
    )

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 0
    content = out_file.read_text(encoding="utf-8")
    cache_content = json.loads(cache_file.read_text(encoding="utf-8"))
    assert '"classification":"accepted-indirect"' in content
    assert cache_content["entries"]["owner/repo#7"]["classification"] == "accepted-indirect"

def test_default_generate_uses_cache_for_closed_unmerged_live_pr(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    out_file = tmp_path / "index.html"
    repos_file = tmp_path / "repos.txt"
    cache_file = tmp_path / "cache.json"
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")
    cache_file.write_text(
        json.dumps(
            {
                "version": 3,
                "entries": {
                    "owner/repo#7": {
                        "classification": "accepted-indirect",
                        "evidenceKind": "accepted-indirect",
                        "viaLabel": "#9",
                        "viaUrl": "https://github.com/owner/repo/pull/9",
                        "release": "",
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        generate,
        "run_gh",
        lambda *_args, **_kwargs: json.dumps([_gh_pr_list_item(number=7, state="CLOSED", closedAt="2026-07-02T17:00:00Z")]),
    )

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 0
    content = out_file.read_text(encoding="utf-8")
    assert '"classification":"accepted-indirect"' in content
    assert '"viaLabel":"#9"' in content

def test_classify_cache_cli_routes_to_rebuild_worker(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_rebuild(**kwargs: object) -> CacheRebuildResult:
        calls.append(kwargs)
        out_path = kwargs["out_cache_file"]
        assert isinstance(out_path, Path)
        out_path.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
        return CacheRebuildResult(checked=3, skipped=2, failed=0, divergences=1)

    monkeypatch.setattr(generate, "rebuild_classification_cache", fake_rebuild)
    cache_file = tmp_path / "input.json"
    out_cache = tmp_path / "out.json"
    divergences = tmp_path / "divergences.json"
    repos_file = tmp_path / "repos.txt"

    result = generate.main(
        [
            "--classify-cache",
            "--cache-file",
            str(cache_file),
            "--out-cache-file",
            str(out_cache),
            "--divergence-file",
            str(divergences),
            "--repos-file",
            str(repos_file),
            "--active-repos-only",
            "--limit",
            "3",
            "--workers",
            "7",
        ],
    )

    assert result == 0
    assert calls == [
        {
            "cache_file": cache_file,
            "out_cache_file": out_cache,
            "divergence_file": divergences,
            "repos_file": repos_file,
            "active_repos_only": True,
            "limit": 3,
            "workers": 7,
        },
    ]
    assert out_cache.read_text(encoding="utf-8") == '{"version":3,"entries":{}}\n'


def test_classify_cache_cli_defaults_to_promoting_rebuild_cache(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.chdir(tmp_path)

    def fake_rebuild(**kwargs: object) -> CacheRebuildResult:
        calls.append(kwargs)
        out_path = kwargs["out_cache_file"]
        assert isinstance(out_path, Path)
        out_path.write_text('{"version":3,"entries":{"owner/repo#1":{"classification":"shipped"}}}\n', encoding="utf-8")
        return CacheRebuildResult(checked=1, skipped=0, failed=0, divergences=0)

    monkeypatch.setattr(generate, "rebuild_classification_cache", fake_rebuild)
    cache_file = tmp_path / "cache.json"
    repos_file = tmp_path / "repos.txt"

    result = generate.main(
        [
            "--classify-cache",
            "--cache-file",
            str(cache_file),
            "--repos-file",
            str(repos_file),
        ],
    )

    assert result == 0
    assert calls[0]["out_cache_file"] == generate.DEFAULT_REBUILD_CACHE_FILE
    assert '"owner/repo#1"' in cache_file.read_text(encoding="utf-8")


def test_classify_cache_cli_handles_interrupt_without_traceback(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    def fake_rebuild(**_kwargs: object) -> CacheRebuildResult:
        raise CacheRebuildInterrupted(CacheRebuildResult(checked=12, skipped=2, failed=0, divergences=3))

    monkeypatch.setattr(generate, "rebuild_classification_cache", fake_rebuild)
    cache_file = tmp_path / "input.json"
    out_cache = tmp_path / "out.json"
    divergences = tmp_path / "divergences.json"
    repos_file = tmp_path / "repos.txt"

    result = generate.main(
        [
            "--classify-cache",
            "--cache-file",
            str(cache_file),
            "--out-cache-file",
            str(out_cache),
            "--divergence-file",
            str(divergences),
            "--repos-file",
            str(repos_file),
        ],
    )

    captured = capsys.readouterr()
    assert result == 130
    assert "Interrupted after classifying 12 PRs, skipped 2, failed 0, divergences 3. Checkpoint saved to" in captured.err


def _repos_file_text(repos: list[str]) -> str:
    return "\n".join(repos) + "\n"


def _gh_pr_list_item(
    *,
    number: int,
    state: str,
    title: str = "Test PR",
    createdAt: str = "2026-07-02T16:00:00Z",
    closedAt: str | None = None,
    mergedAt: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "title": title,
        "createdAt": createdAt,
        "closedAt": closedAt,
        "mergedAt": mergedAt,
        "headRefName": "branch",
        "author": {"login": "rodboev"},
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
        "url": f"https://github.com/owner/repo/pull/{number}",
    }
