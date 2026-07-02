from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from core.github import (
    NETWORK_ERROR_REASON,
    RATE_LIMIT_REASON,
    SERVER_ERROR_REASON,
    TIMEOUT_REASON,
    GhResult,
    _run_gh_with_runner,
    get_gh_retry_delay_seconds,
    get_gh_retry_reason,
    parse_graphql_search_page_json,
    parse_pr_view_json,
)


def _payload_text(path: Path) -> str:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["command"]
    return json.dumps(fixture["payload"])


def test_pr_view_fixture_parses_real_gh_shape(repo_root: Path) -> None:
    parsed = parse_pr_view_json(_payload_text(repo_root / "tests" / "fixtures" / "gh-pr-view-3939.json"))

    assert parsed.number == 3939
    assert parsed.state == "CLOSED"
    assert parsed.author.login == "rodboev"
    assert parsed.mergedAt is None
    assert parsed.changedFiles == 2


def test_graphql_search_fixture_parses_real_gh_shape(repo_root: Path) -> None:
    parsed = parse_graphql_search_page_json(_payload_text(repo_root / "tests" / "fixtures" / "gh-graphql-search-page.json"))

    assert parsed.data.search.issueCount == 289
    assert [node.number for node in parsed.data.search.nodes] == [5408, 5407]
    assert parsed.data.search.nodes[0].author is not None
    assert parsed.data.search.nodes[0].author.login == "rodboev"


@pytest.mark.parametrize(
    ("stderr", "timed_out", "expected"),
    [
        ("secondary rate limit exceeded", False, RATE_LIMIT_REASON),
        ("HTTP 502 Bad Gateway", False, SERVER_ERROR_REASON),
        ("status code 503", False, SERVER_ERROR_REASON),
        ("context deadline exceeded", False, NETWORK_ERROR_REASON),
        ("connection reset by peer", False, NETWORK_ERROR_REASON),
        ("", True, TIMEOUT_REASON),
        ("GraphQL: Could not resolve to a Repository", False, None),
        ("", False, None),
    ],
)
def test_retry_reason_classifier(stderr: str, timed_out: bool, expected: str | None) -> None:
    assert get_gh_retry_reason(stderr, timed_out=timed_out) == expected


@pytest.mark.parametrize(
    ("attempt", "reason", "expected"),
    [
        (1, NETWORK_ERROR_REASON, 5),
        (2, NETWORK_ERROR_REASON, 10),
        (7, NETWORK_ERROR_REASON, 300),
        (8, NETWORK_ERROR_REASON, 300),
        (1, RATE_LIMIT_REASON, 60),
        (5, RATE_LIMIT_REASON, 80),
    ],
)
def test_retry_delay_sequence(attempt: int, reason: str, expected: int) -> None:
    assert get_gh_retry_delay_seconds(attempt, reason) == expected


def test_run_gh_retries_retryable_failure_then_returns_trimmed_stdout() -> None:
    calls = 0
    sleeps: list[int] = []

    def runner(args: Sequence[str], timeout: int) -> GhResult:
        nonlocal calls
        assert args == ("pr", "list")
        assert timeout == 120
        calls += 1
        if calls == 1:
            return GhResult(stdout="", stderr="HTTP 502", returncode=1)
        return GhResult(stdout="[]\n", stderr="", returncode=0)

    result = _run_gh_with_runner(
        ("pr", "list"),
        timeout=120,
        suppress_errors=True,
        runner=runner,
        sleeper=sleeps.append,
    )

    assert result == "[]"
    assert calls == 2
    assert sleeps == [5]


def test_run_gh_non_retryable_failure_returns_stdout_without_sleep() -> None:
    sleeps: list[int] = []

    def runner(args: Sequence[str], timeout: int) -> GhResult:
        del args, timeout
        return GhResult(stdout="partial\n", stderr="not found", returncode=1)

    result = _run_gh_with_runner(
        ("api", "missing"),
        timeout=120,
        suppress_errors=True,
        runner=runner,
        sleeper=sleeps.append,
    )

    assert result == "partial"
    assert sleeps == []
