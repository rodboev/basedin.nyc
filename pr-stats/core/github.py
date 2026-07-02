from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

GH_INVOKE_TIMEOUT_SECONDS: Final = 120
GH_RETRY_BASE_DELAY_SECONDS: Final = 5
GH_RETRY_MAX_DELAY_SECONDS: Final = 300

RATE_LIMIT_REASON: Final = "GitHub rate limit"
SERVER_ERROR_REASON: Final = "GitHub server error"
NETWORK_ERROR_REASON: Final = "network error"
TIMEOUT_REASON: Final = "gh timeout"


@dataclass(frozen=True)
class GhResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


class GithubModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class GhUser(GithubModel):
    login: str = ""
    id: str = ""
    is_bot: bool | None = None
    name: str | None = None


class GhPullRequestView(GithubModel):
    number: int
    state: str
    title: str
    createdAt: str = ""
    closedAt: str | None = None
    mergedAt: str | None = None
    author: GhUser
    additions: int = 0
    deletions: int = 0
    changedFiles: int = 0
    headRefName: str = ""
    url: str = ""
    body: str = ""


class GraphQLPullRequestNode(GithubModel):
    number: int
    state: str
    title: str
    author: GhUser | None = None


class GraphQLSearch(GithubModel):
    issueCount: int
    nodes: list[GraphQLPullRequestNode] = Field(default_factory=list)


class GraphQLSearchData(GithubModel):
    search: GraphQLSearch


class GraphQLSearchPage(GithubModel):
    data: GraphQLSearchData


def get_gh_retry_reason(stderr: str, *, timed_out: bool = False) -> str | None:
    if timed_out:
        return TIMEOUT_REASON
    if not stderr or not stderr.strip():
        return None

    message = stderr.strip().lower()
    if any(fragment in message for fragment in ("secondary rate limit", "rate limit exceeded", "api rate limit", "abuse detection")):
        return RATE_LIMIT_REASON
    if (
        "bad gateway" in message
        or "service unavailable" in message
        or "gateway timeout" in message
        or _contains_http_5xx(message)
        or _contains_status_code_5xx(message)
    ):
        return SERVER_ERROR_REASON
    if (
        "timed out" in message
        or "timeout" in message
        or "context deadline exceeded" in message
        or "i/o timeout" in message
        or "tls handshake timeout" in message
        or "unexpected eof" in message
        or "request canceled" in message
        or "temporary failure" in message
        or "temporarily unavailable" in message
        or _contains_connection_failure(message)
    ):
        return NETWORK_ERROR_REASON
    return None


def get_gh_retry_delay_seconds(attempt: int, reason: str) -> int:
    exponent = min(attempt - 1, 6)
    delay = min(GH_RETRY_MAX_DELAY_SECONDS, GH_RETRY_BASE_DELAY_SECONDS * (2**exponent))
    if reason == RATE_LIMIT_REASON:
        delay = max(60, delay)
    return int(delay)


def run_gh(*args: str, timeout: int = GH_INVOKE_TIMEOUT_SECONDS, suppress_errors: bool = False) -> str:
    return _run_gh_with_runner(args, timeout=timeout, suppress_errors=suppress_errors, runner=_subprocess_runner, sleeper=time.sleep)


def parse_pr_view_json(text: str) -> GhPullRequestView:
    return GhPullRequestView.model_validate_json(text)


def parse_graphql_search_page_json(text: str) -> GraphQLSearchPage:
    return GraphQLSearchPage.model_validate_json(text)


def _run_gh_with_runner(
    args: Sequence[str],
    *,
    timeout: int,
    suppress_errors: bool,
    runner: Callable[[Sequence[str], int], GhResult],
    sleeper: Callable[[int], None],
) -> str:
    attempt = 0
    while True:
        attempt += 1
        result = runner(args, timeout)
        if result.returncode == 0:
            if result.stderr and not suppress_errors:
                sys.stderr.write(result.stderr)
            return result.stdout.rstrip()

        reason = get_gh_retry_reason(result.stderr, timed_out=result.timed_out)
        if reason:
            sleeper(get_gh_retry_delay_seconds(attempt, reason))
            continue

        if result.stderr and not suppress_errors:
            sys.stderr.write(result.stderr)
        return result.stdout.rstrip()


def _subprocess_runner(args: Sequence[str], timeout: int) -> GhResult:
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_gh_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return GhResult(stdout=stdout, stderr=stderr, returncode=124, timed_out=True)
    return GhResult(stdout=completed.stdout, stderr=completed.stderr, returncode=completed.returncode)


def _gh_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _contains_http_5xx(message: str) -> bool:
    return any(f"http 5{digit}{digit2}" in message for digit in range(10) for digit2 in range(10))


def _contains_status_code_5xx(message: str) -> bool:
    return any(f"status code 5{digit}{digit2}" in message for digit in range(10) for digit2 in range(10))


def _contains_connection_failure(message: str) -> bool:
    if "connection" not in message:
        return False
    return any(word in message for word in ("reset", "closed", "refused", "aborted"))
