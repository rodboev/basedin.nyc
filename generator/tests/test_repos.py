from __future__ import annotations

import pytest
from pytest import MonkeyPatch

import core.repos as repos_mod
from core.repos import display_repo, resolve_canonical_repos, set_repo_display_names

RENAMES = {"microsoft/presidio": "data-privacy-stack/presidio"}


def _fake_resolver(monkeypatch: MonkeyPatch, names: dict[str, str]) -> None:
    monkeypatch.setattr(repos_mod, "_canonical_repo_name", lambda repo: names.get(repo, repo))


def test_resolve_canonical_repos_follows_transfer_redirect(monkeypatch: MonkeyPatch) -> None:
    _fake_resolver(monkeypatch, RENAMES)

    assert resolve_canonical_repos(["owner/repo", "microsoft/presidio"]) == {
        "owner/repo": "owner/repo",
        "microsoft/presidio": "data-privacy-stack/presidio",
    }


def test_resolve_canonical_repos_rejects_entry_missing_from_github(monkeypatch: MonkeyPatch) -> None:
    _fake_resolver(monkeypatch, {"presidio": ""})

    with pytest.raises(ValueError, match="not found on GitHub: presidio"):
        resolve_canonical_repos(["owner/repo", "presidio"])


def test_resolve_canonical_repos_rejects_two_entries_for_one_repo(monkeypatch: MonkeyPatch) -> None:
    _fake_resolver(monkeypatch, RENAMES)

    with pytest.raises(ValueError, match="both resolve to data-privacy-stack/presidio"):
        resolve_canonical_repos(["microsoft/presidio", "data-privacy-stack/presidio"])


def test_display_repo_reports_the_name_as_written_in_repos_txt() -> None:
    set_repo_display_names({"data-privacy-stack/presidio": "microsoft/presidio"})

    assert display_repo("data-privacy-stack/presidio") == "microsoft/presidio"
    assert display_repo("owner/unmapped") == "owner/unmapped"
