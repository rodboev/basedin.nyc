from __future__ import annotations

from core.report import format_eastern_date, pull_request_effective_iso_date, repo_label, scalar_value


def test_repo_label_matches_ps1_short_names() -> None:
    assert repo_label("nesquena/hermes-webui") == "webui"
    assert repo_label("github/github-mcp-server") == "gh-mcp"
    assert repo_label("lsdefine/GenericAgent") == "generic-agent"
    assert repo_label("thedotmack/claude-mem") == "claude-mem"


def test_pull_request_effective_iso_date_matches_status_rules() -> None:
    assert pull_request_effective_iso_date(status_key="open", created_at="created", closed_at="closed") == "created"
    assert pull_request_effective_iso_date(status_key="done", created_at="created", closed_at="closed") == "closed"
    assert pull_request_effective_iso_date(status_key="done", created_at="created", closed_at="") == "created"


def test_format_eastern_date_matches_ps1_format() -> None:
    assert format_eastern_date("2026-07-02T13:27:37Z") == "7/2/26 9:27 AM"
    assert format_eastern_date("") == ""
    assert format_eastern_date("not-a-date") == ""


def test_scalar_value_matches_ps1_first_array_item_behavior() -> None:
    assert scalar_value(None) == ""
    assert scalar_value([]) == ""
    assert scalar_value(["a", "b"]) == "a"
    assert scalar_value((["nested"],)) == "nested"
    assert scalar_value(3) == 3

