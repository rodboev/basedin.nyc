from __future__ import annotations

from pathlib import Path

from core.report import (
    PrReportItem,
    format_eastern_date,
    pr_filter_count,
    pr_repo_matches,
    pr_status_matches,
    pull_request_effective_iso_date,
    repo_label,
    report_items_from_script_dicts,
    report_items_to_script_dicts,
    scalar_value,
    sort_report_items_by_effective_date,
    sort_repos_by_accepted_count,
    status_filter_dicts,
)
from core.timeline import load_pr_data_from_html


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


def test_pr_filter_matching_matches_ps1_status_and_repo_rules() -> None:
    assert pr_status_matches(filter_status_key="not-shipped", item_status_key="lost", not_shipped_statuses=("lost", "replaced"))
    assert pr_status_matches(filter_status_key="not-shipped", item_status_key="replaced", not_shipped_statuses=("lost", "replaced"))
    assert not pr_status_matches(filter_status_key="not-shipped", item_status_key="open", not_shipped_statuses=("lost", "replaced"))
    assert pr_status_matches(filter_status_key="open", item_status_key="open", not_shipped_statuses=("lost",))
    assert pr_repo_matches(filter_repo_key="all", item_repo_label="webui")
    assert pr_repo_matches(filter_repo_key="webui", item_repo_label="webui")
    assert not pr_repo_matches(filter_repo_key="webui", item_repo_label="other")


def test_pr_filter_count_uses_status_and_repo_filters() -> None:
    items = [
        _item(number=1, statusKey="done", repoLabel="a"),
        _item(number=2, statusKey="lost", repoLabel="a"),
        _item(number=3, statusKey="lost", repoLabel="b"),
    ]

    assert pr_filter_count(items, status_key="not-shipped", repo_key="a", not_shipped_statuses=("lost",)) == 1
    assert pr_filter_count(items, status_key="not-shipped", repo_key="all", not_shipped_statuses=("lost",)) == 2
    assert pr_filter_count(items, status_key="done", repo_key="all", not_shipped_statuses=("lost",)) == 1


def test_report_items_serialize_to_existing_script_shape() -> None:
    assert report_items_to_script_dicts([_item(number=7, title="Fix")]) == [
        {
            "number": 7,
            "url": "https://github.com/owner/repo/pull/7",
            "repo": "owner/repo",
            "repoLabel": "repo",
            "title": "Fix",
            "classification": "done",
            "statusKey": "done",
            "statusLabel": "Done",
            "statusClass": "tag-done",
            "dateLabel": "7/2/26 9:27 AM",
            "releaseLabel": "",
            "viaLabel": "",
            "viaUrl": "",
            "createdAt": "2026-07-01T00:00:00Z",
            "closedAt": "2026-07-02T13:27:37Z",
            "mergedAt": "",
            "additions": 1,
            "deletions": 2,
            "changedFiles": 3,
        },
    ]


def test_report_items_round_trip_existing_script_shape(repo_root: Path) -> None:
    raw_items = load_pr_data_from_html((repo_root / "index.html").read_text(encoding="utf-8"))
    typed_items = report_items_from_script_dicts(raw_items)

    assert report_items_to_script_dicts(typed_items[:5]) == raw_items[:5]


def test_sort_report_items_by_effective_date_uses_open_creation_and_closed_close() -> None:
    items = [
        _item(number=1, statusKey="done", createdAt="2026-07-03T00:00:00Z", closedAt="2026-07-04T00:00:00Z"),
        _item(number=2, statusKey="open", createdAt="2026-07-05T00:00:00Z", closedAt=""),
        _item(number=3, statusKey="done", createdAt="2026-07-06T00:00:00Z", closedAt="2026-07-01T00:00:00Z"),
    ]

    assert [item.number for item in sort_report_items_by_effective_date(items)] == [2, 1, 3]


def test_status_filter_dicts_matches_existing_script_shape() -> None:
    items = [
        _item(number=1, statusKey="done", repoLabel="a"),
        _item(number=2, statusKey="lost", repoLabel="a"),
        _item(number=3, statusKey="lost", repoLabel="b"),
    ]

    assert status_filter_dicts(
        items,
        (("done", "Done"), ("not-shipped", "Not Done")),
        repo_key="all",
        not_shipped_statuses=("lost",),
    ) == [
        {"key": "done", "label": "Done", "count": 1},
        {"key": "not-shipped", "label": "Not Done", "count": 2},
    ]


def test_sort_repos_by_accepted_count_matches_generated_display_order(repo_root: Path) -> None:
    items = load_pr_data_from_html((repo_root / "index.html").read_text(encoding="utf-8"))
    repos = [
        "nesquena/hermes-webui",
        "kenn-io/agentsview",
        "thedotmack/claude-mem",
        "headroomlabs-ai/headroom",
        "mem0ai/mem0",
        "stablyai/orca",
    ]

    sorted_repos = sort_repos_by_accepted_count(repos, items, accepted_classifications=("shipped", "accepted-indirect"))

    assert sorted_repos[0] == "nesquena/hermes-webui"
    assert sorted_repos == sorted(
        repos,
        key=lambda repo: (
            -sum(1 for item in items if item["repo"] == repo and item["classification"] in {"shipped", "accepted-indirect"}),
            repo,
        ),
    )


def _item(**overrides: object) -> PrReportItem:
    data: dict[str, object] = {
        "number": 1,
        "url": "https://github.com/owner/repo/pull/1",
        "repo": "owner/repo",
        "repoLabel": "repo",
        "title": "Title",
        "classification": "done",
        "statusKey": "done",
        "statusLabel": "Done",
        "statusClass": "tag-done",
        "dateLabel": "7/2/26 9:27 AM",
        "releaseLabel": "",
        "viaLabel": "",
        "viaUrl": "",
        "createdAt": "2026-07-01T00:00:00Z",
        "closedAt": "2026-07-02T13:27:37Z",
        "mergedAt": "",
        "additions": 1,
        "deletions": 2,
        "changedFiles": 3,
    }
    data.update(overrides)
    if "url" not in overrides:
        data["url"] = f"https://github.com/owner/repo/pull/{data['number']}"
    return PrReportItem(
        number=int(data["number"]),
        url=str(data["url"]),
        repo=str(data["repo"]),
        repoLabel=str(data["repoLabel"]),
        title=str(data["title"]),
        classification=str(data["classification"]),
        statusKey=str(data["statusKey"]),
        statusLabel=str(data["statusLabel"]),
        statusClass=str(data["statusClass"]),
        dateLabel=str(data["dateLabel"]),
        releaseLabel=str(data["releaseLabel"]),
        viaLabel=str(data["viaLabel"]),
        viaUrl=str(data["viaUrl"]),
        createdAt=str(data["createdAt"]),
        closedAt=str(data["closedAt"]),
        mergedAt=str(data["mergedAt"]),
        additions=int(data["additions"]),
        deletions=int(data["deletions"]),
        changedFiles=int(data["changedFiles"]),
    )
