from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import re

from pytest import CaptureFixture, MonkeyPatch

import generate
import core.leaderboard as leaderboard_mod
import core.releases as releases_mod
from core.classification_rebuild import CacheRebuildInterrupted, CacheRebuildResult
from core.github import GhPullRequestView, GhRetryExhausted
from core.classify import ClassificationResult
from core.models import Cache, ClassificationEntry, Evidence
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

    mock_gh = _mock_graphql_search(
        "owner/repo",
        [
            _gh_pr_list_item(
                number=101,
                state="MERGED",
                title="Fresh merge absent from stale HTML",
                closedAt="2026-07-02T17:00:00Z",
                mergedAt="2026-07-02T17:00:00Z",
            ),
        ],
    )
    monkeypatch.setattr(generate, "run_gh", mock_gh)
    monkeypatch.setattr(leaderboard_mod, "run_gh", mock_gh)
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})

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

def test_default_generate_survives_leaderboard_retry_exhaustion(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    out_file = tmp_path / "index.html"
    cache_file = tmp_path / "cache.json"
    repos_file = tmp_path / "repos.txt"
    cache_file.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")
    monkeypatch.setattr(
        generate,
        "run_gh",
        _mock_graphql_search("owner/repo", [_gh_pr_list_item(number=101, state="MERGED", mergedAt="2026-07-02T17:00:00Z")]),
    )

    def _exhausted(*_args: str, **_kwargs: object) -> str:
        raise GhRetryExhausted("gh api graphql failed after 5 attempts: GitHub rate limit")

    monkeypatch.setattr(leaderboard_mod, "run_gh", _exhausted)
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 0
    assert "leaderboard refresh failed for owner/repo" in capsys.readouterr().err


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
    mock_gh = _mock_graphql_search("owner/repo", [_gh_pr_list_item(number=101, state="MERGED", mergedAt="2026-07-02T17:00:00Z")])
    monkeypatch.setattr(generate, "run_gh", mock_gh)
    monkeypatch.setattr(leaderboard_mod, "run_gh", mock_gh)
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})

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
    capsys: CaptureFixture[str],
) -> None:
    out_file = tmp_path / "index.html"
    repos_file = tmp_path / "repos.txt"
    cache_file = tmp_path / "cache.json"
    repos_file.write_text(_repos_file_text(["owner/repo"]), encoding="utf-8")
    cache_file.write_text('{"version":3,"entries":{}}\n', encoding="utf-8")
    mock_gh = _mock_graphql_search("owner/repo", [_gh_pr_list_item(number=7, state="CLOSED", closedAt="2026-07-02T17:00:00Z")])
    monkeypatch.setattr(generate, "run_gh", mock_gh)
    monkeypatch.setattr(leaderboard_mod, "run_gh", mock_gh)
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})
    monkeypatch.setattr(generate, "live_evidence", lambda *_args, **_kwargs: Evidence())
    monkeypatch.setattr(
        generate,
        "classify_closed_pr",
        lambda *_args, **_kwargs: ClassificationResult(
            classification="accepted-indirect",
            evidence_kind="accepted-indirect",
            via_label="#9",
            via_url="https://github.com/owner/repo/pull/9",
            log_label="accepted-indirect",
        ),
    )

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 0
    captured = capsys.readouterr()
    content = out_file.read_text(encoding="utf-8")
    cache_content = json.loads(cache_file.read_text(encoding="utf-8"))
    assert '"classification":"accepted-indirect"' in content
    assert cache_content["entries"]["owner/repo#7"]["classification"] == "accepted-indirect"
    assert "  [1/1] #7 (repo, @rodboev)... accepted-indirect" in captured.err

def test_default_generate_uses_cache_for_closed_unmerged_live_pr(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
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
    mock_gh = _mock_graphql_search("owner/repo", [_gh_pr_list_item(number=7, state="CLOSED", closedAt="2026-07-02T17:00:00Z")])
    monkeypatch.setattr(generate, "run_gh", mock_gh)
    monkeypatch.setattr(leaderboard_mod, "run_gh", mock_gh)
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    assert result == 0
    captured = capsys.readouterr()
    content = out_file.read_text(encoding="utf-8")
    assert '"classification":"accepted-indirect"' in content
    assert '"viaLabel":"#9"' in content
    assert "#7" not in captured.err


def test_seed_author_pull_cache_from_existing_index(tmp_path: Path) -> None:
    out_file = tmp_path / "index.html"
    item = _pr_data_item(number=7, repo="owner/repo", classification="shipped", mergedAt="2026-07-02T17:00:00Z")
    out_file.write_text(f"var PR_DATA = {json.dumps([item])};\n", encoding="utf-8")
    cache = Cache()

    changed = generate.seed_author_pull_cache_from_html(cache, out_file=out_file, repos=["owner/repo"], author="rodboev")

    row = cache.authorPulls["owner/repo#7"]
    assert changed
    assert row["state"] == "MERGED"
    assert row["title"] == "Test PR"
    assert row["updatedAt"] == ""
    assert row["author"] == {"login": "rodboev"}


def test_incremental_fetch_returns_cached_closed_rows_and_graphql_open_rows(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    cache = Cache(
        authorPulls={
            "owner/repo#1": _author_pull_cache_row("owner/repo", _gh_pr_list_item(number=1, state="CLOSED", closedAt="2026-07-02T17:00:00Z")),
        },
        authorPullScanMeta={"owner/repo": {"scannedAt": "2026-07-08T10:00:00Z"}},
    )
    calls: list[str] = []

    def mock_gh(*args: str, **_kwargs: object) -> str:
        joined = " ".join(args)
        calls.append(joined)
        if "is:open" in joined:
            return _graphql_search_json("owner/repo", [_gh_pr_list_item(number=2, state="OPEN")])
        return _graphql_search_json("owner/repo", [])

    monkeypatch.setattr(generate, "run_gh", mock_gh)

    pulls, failed, changed = generate.fetch_author_pull_requests(
        ["owner/repo"],
        author="rodboev",
        cache=cache,
        out_file=tmp_path / "missing.html",
        now=now,
        workers=1,
    )

    assert failed == ()
    assert changed
    assert {pr.number for _repo, pr in pulls} == {1, 2}
    assert any("updated:>=2026-07-06T10:00:00Z" in call for call in calls)
    assert cache.authorPullScanMeta["owner/repo"]["scannedAt"] == "2026-07-08T12:00:00Z"


def test_generate_updates_cached_open_pr_that_graphql_reports_merged(
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
                "authorPulls": {
                    "owner/repo#7": _author_pull_cache_row("owner/repo", _gh_pr_list_item(number=7, state="OPEN")),
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        generate,
        "run_gh",
        _mock_graphql_search("owner/repo", [_gh_pr_list_item(number=7, state="MERGED", mergedAt="2026-07-08T12:00:00Z")]),
    )
    monkeypatch.setattr(leaderboard_mod, "run_gh", _mock_graphql_search("owner/repo", []))
    monkeypatch.setattr(releases_mod, "fetch_repo_releases", lambda _repo, **_kw: None)
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", tmp_path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})

    result = generate.generate_report(
        cache_file=cache_file,
        template_file=repo_root / "template.html",
        out_file=out_file,
        repos_file=repos_file,
    )

    cache_content = json.loads(cache_file.read_text(encoding="utf-8"))
    assert result == 0
    assert cache_content["authorPulls"]["owner/repo#7"]["state"] == "MERGED"
    assert cache_content["entries"]["owner/repo#7"]["classification"] == "shipped"


def test_incremental_fetch_failure_keeps_cached_rows_and_scan_timestamp(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cache = Cache(
        authorPulls={
            "owner/repo#1": _author_pull_cache_row("owner/repo", _gh_pr_list_item(number=1, state="OPEN")),
        },
        authorPullScanMeta={"owner/repo": {"scannedAt": "2026-07-08T10:00:00Z"}},
    )

    def exhausted(*_args: str, **_kwargs: object) -> str:
        raise GhRetryExhausted("rate limited")

    monkeypatch.setattr(generate, "run_gh", exhausted)

    pulls, failed, changed = generate.fetch_author_pull_requests(
        ["owner/repo"],
        author="rodboev",
        cache=cache,
        out_file=tmp_path / "missing.html",
        now=datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
        workers=1,
    )

    assert failed == ("owner/repo",)
    assert not changed
    assert [pr.number for _repo, pr in pulls] == [1]
    assert cache.authorPullScanMeta["owner/repo"]["scannedAt"] == "2026-07-08T10:00:00Z"


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
    updatedAt: str = "2026-07-02T17:00:00Z",
    closedAt: str | None = None,
    mergedAt: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "title": title,
        "createdAt": createdAt,
        "updatedAt": updatedAt,
        "closedAt": closedAt,
        "mergedAt": mergedAt,
        "headRefName": "branch",
        "author": {"login": "rodboev"},
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
        "url": f"https://github.com/owner/repo/pull/{number}",
    }


def _pr_data_item(
    *,
    number: int,
    repo: str,
    classification: str,
    mergedAt: str = "",
    closedAt: str = "2026-07-02T17:00:00Z",
) -> dict[str, object]:
    return {
        "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "repo": repo,
        "repoLabel": repo.rsplit("/", 1)[-1],
        "title": "Test PR",
        "classification": classification,
        "statusKey": "shipped" if classification == "accepted-indirect" else classification,
        "statusLabel": "Shipped",
        "statusClass": "tag-shipped",
        "dateLabel": "7/2/26 1:00 PM",
        "releaseLabel": "",
        "viaLabel": "",
        "viaUrl": "",
        "createdAt": "2026-07-02T16:00:00Z",
        "closedAt": closedAt,
        "mergedAt": mergedAt,
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
    }


def _author_pull_cache_row(repo: str, item: dict[str, object]) -> dict[str, object]:
    row = dict(item)
    row["repo"] = repo
    return row


def _mock_graphql_search(repo: str, items: list[dict[str, object]]) -> Callable[..., str]:
    def mock(*_args: str, **_kwargs: object) -> str:
        return _graphql_search_json(repo, items)

    return mock


def _graphql_search_json(repo: str, items: list[dict[str, object]]) -> str:
    nodes = []
    for item in items:
        node = dict(item)
        node["repository"] = {"nameWithOwner": repo}
        nodes.append(node)
    return json.dumps({
        "data": {
            "search": {
                "issueCount": len(nodes),
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": nodes,
            },
        },
    })


def _closed_view(closed_at: str) -> GhPullRequestView:
    return GhPullRequestView.model_validate(
        {
            "number": 10,
            "state": "CLOSED",
            "title": "Fix bug",
            "closedAt": closed_at,
            "author": {"login": "rodboev"},
        }
    )


def test_withdrawn_entry_within_recheck_window_reclassifies_live(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    cache = Cache()
    cache.entries["owner/repo#10"] = ClassificationEntry(classification="withdrawn", evidenceKind="author-withdrawn", cachedAt="2026-07-12T00:00:00Z")
    view = _closed_view("2026-07-12T00:00:00Z")
    monkeypatch.setattr(generate, "live_evidence", lambda repo, number, pr: Evidence())
    monkeypatch.setattr(
        generate,
        "classify_closed_pr",
        lambda pr, evidence: ClassificationResult(
            classification="accepted-indirect",
            via_label="#9103",
            via_url="https://github.com/owner/repo/pull/9103",
            evidence_kind="accepted-indirect",
            log_label="accepted indirectly via #9103 (replacement credits author)",
        ),
    )

    assert generate._needs_live_classification("owner/repo", view, cache, now)
    result, updated = generate.live_pull_request_classification("owner/repo", view, cache, now=now)

    assert updated
    assert result.classification == "accepted-indirect"
    assert cache.entries["owner/repo#10"].classification == "accepted-indirect"


def test_withdrawn_entry_rechecked_within_interval_stays_cached(monkeypatch: MonkeyPatch) -> None:
    # Young withdrawal, but cachedAt is fresher than WITHDRAWN_RECHECK_INTERVAL_HOURS:
    # the run must serve from cache instead of reclassifying again.
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    cache = Cache()
    cache.entries["owner/repo#10"] = ClassificationEntry(classification="withdrawn", evidenceKind="author-withdrawn", cachedAt="2026-07-16T22:00:00Z")
    view = _closed_view("2026-07-16T00:00:00Z")

    def fail_classify(pr: object, evidence: object) -> ClassificationResult:
        raise AssertionError("recheck within the interval must not reclassify")

    monkeypatch.setattr(generate, "classify_closed_pr", fail_classify)

    assert not generate._needs_live_classification("owner/repo", view, cache, now)
    result, updated = generate.live_pull_request_classification("owner/repo", view, cache, now=now)

    assert not updated
    assert result.from_cache
    assert result.classification == "withdrawn"


def test_withdrawn_entry_rechecked_after_interval_reclassifies(monkeypatch: MonkeyPatch) -> None:
    # Young withdrawal whose last recheck is older than the interval: recheck fires.
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    cache = Cache()
    cache.entries["owner/repo#10"] = ClassificationEntry(classification="withdrawn", evidenceKind="author-withdrawn", cachedAt="2026-07-16T12:00:00Z")
    view = _closed_view("2026-07-16T00:00:00Z")
    monkeypatch.setattr(generate, "live_evidence", lambda repo, number, pr: Evidence())
    monkeypatch.setattr(
        generate,
        "classify_closed_pr",
        lambda pr, evidence: ClassificationResult(
            classification="accepted-indirect",
            via_label="#9103",
            via_url="https://github.com/owner/repo/pull/9103",
            evidence_kind="accepted-indirect",
            log_label="accepted indirectly via #9103 (replacement credits author)",
        ),
    )

    assert generate._needs_live_classification("owner/repo", view, cache, now)
    result, updated = generate.live_pull_request_classification("owner/repo", view, cache, now=now)

    assert updated
    assert result.classification == "accepted-indirect"


def test_withdrawn_entry_past_recheck_window_stays_cached(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    cache = Cache()
    cache.entries["owner/repo#10"] = ClassificationEntry(classification="withdrawn", evidenceKind="author-withdrawn", cachedAt="2026-06-02T00:00:00Z")
    view = _closed_view("2026-06-01T00:00:00Z")

    def fail_classify(pr: object, evidence: object) -> ClassificationResult:
        raise AssertionError("settled withdrawal must not reclassify")

    monkeypatch.setattr(generate, "classify_closed_pr", fail_classify)

    assert not generate._needs_live_classification("owner/repo", view, cache, now)
    result, updated = generate.live_pull_request_classification("owner/repo", view, cache, now=now)

    assert not updated
    assert result.from_cache
    assert result.classification == "withdrawn"


def test_non_withdrawn_entry_is_not_rechecked(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    cache = Cache()
    cache.entries["owner/repo#10"] = ClassificationEntry(classification="lost", evidenceKind="lost", cachedAt="2026-07-16T00:00:00Z")
    view = _closed_view("2026-07-16T00:00:00Z")

    def fail_classify(pr: object, evidence: object) -> ClassificationResult:
        raise AssertionError("non-withdrawn entries must serve from cache")

    monkeypatch.setattr(generate, "classify_closed_pr", fail_classify)

    assert not generate._needs_live_classification("owner/repo", view, cache, now)
    result, updated = generate.live_pull_request_classification("owner/repo", view, cache, now=now)

    assert not updated
    assert result.from_cache
    assert result.classification == "lost"
