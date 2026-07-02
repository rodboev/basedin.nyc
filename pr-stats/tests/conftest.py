from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core.models import Cache, Comment, Evidence, PullRequest, PullRequestRef, TimelineEvent, UserRef


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def live_cache_path(repo_root: Path) -> Path:
    return repo_root / ".pr-classification-cache.json"


@pytest.fixture
def make_cache_file(tmp_path: Path) -> Callable[[bytes], Path]:
    def factory(content: bytes) -> Path:
        path = tmp_path / "cache.json"
        path.write_bytes(content)
        return path

    return factory


@pytest.fixture
def empty_loaded_cache() -> Cache:
    return Cache()


@pytest.fixture
def make_pr() -> Callable[..., PullRequest]:
    def factory(**overrides: object) -> PullRequest:
        data: dict[str, object] = {
            "repo": "owner/repo",
            "number": 10,
            "title": "Fix bug",
            "url": "https://github.com/owner/repo/pull/10",
            "state": "CLOSED",
            "merged": False,
            "mergedAt": "",
            "closedAt": "2026-07-01T00:00:00Z",
            "author": {"login": "rodboev"},
        }
        data.update(overrides)
        return PullRequest.model_validate(data)

    return factory


@pytest.fixture
def make_ref() -> Callable[..., PullRequestRef]:
    def factory(**overrides: object) -> PullRequestRef:
        data: dict[str, object] = {
            "number": 20,
            "title": "Integration PR",
            "url": "https://github.com/owner/repo/pull/20",
            "state": "MERGED",
            "merged": True,
            "mergedAt": "2026-07-01T00:00:00Z",
            "author": {"login": "maintainer"},
        }
        data.update(overrides)
        return PullRequestRef.model_validate(data)

    return factory


@pytest.fixture
def make_comment() -> Callable[..., Comment]:
    def factory(**overrides: object) -> Comment:
        data: dict[str, object] = {
            "body": "Thanks for the PR",
            "author": {"login": "maintainer"},
            "authorAssociation": "MEMBER",
        }
        data.update(overrides)
        return Comment.model_validate(data)

    return factory


@pytest.fixture
def make_evidence() -> Callable[..., Evidence]:
    def factory(**overrides: object) -> Evidence:
        data: dict[str, object] = {
            "comments": [],
            "timeline_items": [],
            "reference_text_by_pr": {},
            "pull_states_by_pr": {},
            "commit_author_logins_by_pr": {},
            "maintainer_logins": {"maintainer"},
            "integration_bots": set(),
        }
        data.update(overrides)
        return Evidence.model_validate(data)

    return factory


@pytest.fixture
def make_event() -> Callable[..., TimelineEvent]:
    def factory(**overrides: object) -> TimelineEvent:
        data: dict[str, object] = {
            "__typename": "ClosedEvent",
            "createdAt": "2026-07-01T00:00:00Z",
        }
        data.update(overrides)
        return TimelineEvent.model_validate(data)

    return factory
