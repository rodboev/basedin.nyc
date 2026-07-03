from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

GH_INVOKE_TIMEOUT_SECONDS: Final = 120
GH_RETRY_BASE_DELAY_SECONDS: Final = 5
GH_RETRY_MAX_DELAY_SECONDS: Final = 300
GH_RETRY_MAX_ATTEMPTS: Final = 5
GH_CANCEL_POLL_SECONDS: Final = 0.5

RATE_LIMIT_REASON: Final = "GitHub rate limit"
SERVER_ERROR_REASON: Final = "GitHub server error"
NETWORK_ERROR_REASON: Final = "network error"
TIMEOUT_REASON: Final = "gh timeout"

_GH_CANCELLED = threading.Event()
_ACTIVE_GH_LOCK = threading.Lock()
_ACTIVE_GH_PROCESSES: set[subprocess.Popen[str]] = set()


@dataclass(frozen=True)
class GhResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


class GithubModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class GhError(RuntimeError):
    pass


class GhCancelled(GhError):
    pass


class GhRetryExhausted(GhError):
    pass


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
    return _run_gh_with_runner(
        args,
        timeout=timeout,
        suppress_errors=suppress_errors,
        runner=_subprocess_runner,
        sleeper=_sleep_unless_cancelled,
        max_attempts=GH_RETRY_MAX_ATTEMPTS,
    )


def cancel_running_gh() -> None:
    _GH_CANCELLED.set()
    with _ACTIVE_GH_LOCK:
        processes = list(_ACTIVE_GH_PROCESSES)
    for process in processes:
        _kill_process_tree(process)


def reset_gh_cancellation() -> None:
    _GH_CANCELLED.clear()


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
    max_attempts: int = GH_RETRY_MAX_ATTEMPTS,
) -> str:
    attempt = 0
    while True:
        if _GH_CANCELLED.is_set():
            raise GhCancelled("gh invocation cancelled")
        attempt += 1
        result = runner(args, timeout)
        if result.returncode == 0:
            if result.stderr and not suppress_errors:
                sys.stderr.write(result.stderr)
            return result.stdout.rstrip()

        reason = get_gh_retry_reason(result.stderr, timed_out=result.timed_out)
        if reason:
            if attempt >= max_attempts:
                if result.stderr and not suppress_errors:
                    sys.stderr.write(result.stderr)
                raise GhRetryExhausted(f"{_format_gh_command_for_log(args)} failed after {attempt} attempts: {reason}")
            delay = get_gh_retry_delay_seconds(attempt, reason)
            sys.stderr.write(f"{_format_gh_command_for_log(args)} retrying in {delay}s after {reason}\n")
            sleeper(delay)
            continue

        if result.stderr and not suppress_errors:
            sys.stderr.write(result.stderr)
        return result.stdout.rstrip()


def _subprocess_runner(args: Sequence[str], timeout: int) -> GhResult:
    if _GH_CANCELLED.is_set():
        raise GhCancelled("gh invocation cancelled")
    startupinfo: subprocess.STARTUPINFO | None = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = _windows_hidden_process_flags()

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [_resolve_gh_executable(), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_gh_environment(),
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        with _ACTIVE_GH_LOCK:
            _ACTIVE_GH_PROCESSES.add(process)
        stdout, stderr = _communicate_with_cancellation(process, timeout)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            _kill_process_tree(process)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return GhResult(stdout=stdout, stderr=stderr, returncode=124, timed_out=True)
    except KeyboardInterrupt:
        if process is not None:
            _kill_process_tree(process)
        raise
    finally:
        if process is not None:
            with _ACTIVE_GH_LOCK:
                _ACTIVE_GH_PROCESSES.discard(process)
    return GhResult(stdout=stdout, stderr=stderr, returncode=process.returncode if process is not None else 1)


def _resolve_gh_executable() -> str:
    executable = shutil.which("gh.exe") if os.name == "nt" else shutil.which("gh")
    return executable or "gh"


def _communicate_with_cancellation(process: subprocess.Popen[str], timeout: int) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    while True:
        if _GH_CANCELLED.is_set():
            _kill_process_tree(process)
            raise GhCancelled("gh invocation cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stdout, stderr = process.communicate(timeout=0)
            return stdout, stderr
        try:
            return process.communicate(timeout=min(GH_CANCEL_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise


def _sleep_unless_cancelled(seconds: int) -> None:
    if _GH_CANCELLED.wait(seconds):
        raise GhCancelled("gh invocation cancelled")


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=_windows_hidden_process_flags(),
        )
        return
    process.kill()


def _gh_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"
    env["GH_BROWSER"] = "echo"
    return env


def _format_gh_command_for_log(args: Sequence[str]) -> str:
    return "gh " + " ".join(args[:4])


def _windows_hidden_process_flags() -> int:
    return subprocess.CREATE_NO_WINDOW


def _contains_http_5xx(message: str) -> bool:
    return any(f"http 5{digit}{digit2}" in message for digit in range(10) for digit2 in range(10))


def _contains_status_code_5xx(message: str) -> bool:
    return any(f"status code 5{digit}{digit2}" in message for digit in range(10) for digit2 in range(10))


def _contains_connection_failure(message: str) -> bool:
    if "connection" not in message:
        return False
    return any(word in message for word in ("reset", "closed", "refused", "aborted"))
