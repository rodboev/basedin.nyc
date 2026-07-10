from __future__ import annotations

import json
import re
from pathlib import Path

from core.classify import ClassificationResult
from core.github import GhPullRequestView
from core.report import (
    PrReportItem,
    RepresentativeItem,
    default_status_filter_dicts,
    enrich_representative_items,
    format_acceptance_rate,
    parse_representative_readme,
    format_eastern_date,
    pr_filter_count,
    pr_repo_matches,
    pr_status_matches,
    pull_request_effective_iso_date,
    repo_filter_dicts,
    repo_label,
    report_activity_summary,
    report_bar_items,
    report_counts,
    report_item_from_pull_request_view,
    report_items_from_script_dicts,
    report_items_to_script_dicts,
    repo_status_rows,
    scalar_value,
    sort_report_items_by_effective_date,
    sort_repos_by_accepted_count,
    status_filter_dicts,
)
from core.timeline import load_pr_data_from_html


def test_parse_representative_readme_matches_ps1_block_parsing() -> None:
    readme = (
        "# Title\n"
        "\n"
        "Representative merged PRs:\n"
        "- [#3571](https://github.com/nesquena/hermes-webui/pull/3571) — adds a "
        "[saved prompts](https://example.com/docs) library ([v0.51.338](https://github.com/nesquena/hermes-webui/releases/tag/v0.51.338))\n"
        "- [#734](https://github.com/kenn-io/agentsview/pull/734) — surfaces Copilot AI-credit estimates\n"
        "\n"
        "## Project Structure\n"
        "- [#999](https://github.com/other/repo/pull/999) — must not be picked up\n"
    )

    items = parse_representative_readme(readme)

    assert [item.number for item in items] == [3571, 734]
    first = items[0]
    assert first.repo == "nesquena/hermes-webui"
    assert first.repoLabel == "hermes-webui"
    assert first.desc == 'adds a <a href="https://example.com/docs">saved prompts</a> library'
    assert first.release == "v0.51.338"
    assert first.releaseUrl == "https://github.com/nesquena/hermes-webui/releases/tag/v0.51.338"
    assert items[1].desc == "surfaces Copilot AI-credit estimates"
    assert items[1].release == ""


def test_enrich_representative_items_pulls_classification_release_and_via() -> None:
    parsed = [
        RepresentativeItem(
            number=7,
            url="https://github.com/owner/repo/pull/7",
            repo="owner/repo",
            repoLabel="repo",
            desc="shipped work",
        ),
        RepresentativeItem(
            number=8,
            url="https://github.com/owner/repo/pull/8",
            repo="owner/repo",
            repoLabel="repo",
            desc="indirect work",
        ),
        RepresentativeItem(
            number=9,
            url="https://github.com/other/repo/pull/9",
            repo="other/repo",
            repoLabel="repo",
            desc="unmatched work",
            release="v9",
            releaseUrl="https://example.com/v9",
        ),
    ]
    report_items = [
        _item(number=7, classification="shipped", releaseLabel="v1.2.3", viaLabel="#70", viaUrl="https://github.com/owner/repo/pull/70"),
        _item(number=8, classification="accepted-indirect", releaseLabel="indirect", viaLabel="#80", viaUrl="https://github.com/owner/repo/pull/80"),
    ]

    enriched = enrich_representative_items(parsed, report_items)

    assert enriched[0].release == "v1.2.3"
    assert enriched[0].releaseUrl == "https://github.com/owner/repo/releases/tag/v1.2.3"
    assert enriched[0].viaLabel == "#70"
    assert enriched[1].release == "indirect"
    assert enriched[1].releaseUrl == ""
    assert enriched[1].viaLabel == "#80"
    assert enriched[1].classification == "accepted-indirect"
    assert enriched[2] == parsed[2]


def test_repo_label_matches_ps1_short_names() -> None:
    assert repo_label("nesquena/hermes-webui") == "hermes-webui"
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
            "releaseUrl": "",
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
    re_serialized = report_items_to_script_dicts(typed_items[:5])
    expected = [{**item, "releaseUrl": item.get("releaseUrl", "")} for item in raw_items[:5]]

    assert re_serialized == expected


def test_report_item_from_pull_request_view_maps_classification_to_pr_data_shape() -> None:
    item = report_item_from_pull_request_view(
        repo="nesquena/hermes-webui",
        pr=_view(
            number=42,
            state="CLOSED",
            title="Fix streaming",
            createdAt="2026-07-01T00:00:00Z",
            closedAt="2026-07-02T13:27:37Z",
            additions=10,
            deletions=2,
            changedFiles=3,
            url="",
        ),
        classification=ClassificationResult(
            classification="shipped",
            release="v1.2.3",
            via_label="direct",
            via_url="https://github.com/nesquena/hermes-webui/pull/42",
            evidence_kind="direct-merge",
        ),
    )

    assert item.to_script_dict() == {
        "number": 42,
        "url": "https://github.com/nesquena/hermes-webui/pull/42",
        "repo": "nesquena/hermes-webui",
        "repoLabel": "hermes-webui",
        "title": "Fix streaming",
        "classification": "shipped",
        "statusKey": "shipped",
        "statusLabel": "Shipped",
        "statusClass": "tag-shipped",
        "dateLabel": "7/2/26 9:27 AM",
        "releaseLabel": "v1.2.3",
        "releaseUrl": "https://github.com/nesquena/hermes-webui/releases/tag/v1.2.3",
        "viaLabel": "direct",
        "viaUrl": "https://github.com/nesquena/hermes-webui/pull/42",
        "createdAt": "2026-07-01T00:00:00Z",
        "closedAt": "2026-07-02T13:27:37Z",
        "mergedAt": "",
        "additions": 10,
        "deletions": 2,
        "changedFiles": 3,
    }
    assert item.evidenceKind == "direct-merge"


def test_report_item_from_pull_request_view_rolls_indirect_into_shipped_status() -> None:
    item = report_item_from_pull_request_view(
        repo="stablyai/orca",
        pr=_view(number=6362, state="CLOSED", closedAt="2026-07-02T13:27:37Z"),
        classification=ClassificationResult(classification="accepted-indirect", via_label="#6574", evidence_kind="accepted-indirect"),
    )

    assert item.classification == "accepted-indirect"
    assert item.statusKey == "shipped"
    assert item.statusLabel == "Shipped"
    assert item.releaseLabel == "indirect"


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


def test_repo_filter_dicts_match_existing_script_shape() -> None:
    assert repo_filter_dicts(["nesquena/hermes-webui", "thedotmack/claude-mem", "github/github-mcp-server"]) == [
        {"key": "all", "label": "All"},
        {"key": "hermes-webui", "label": "hermes-webui"},
        {"key": "claude-mem", "label": "claude-mem"},
        {"key": "gh-mcp", "label": "gh-mcp"},
    ]


def test_report_counts_matches_ps1_summary_math() -> None:
    counts = report_counts(
        [
            _item(number=1, classification="direct"),
            _item(number=2, classification="indirect"),
            _item(number=3, classification="open"),
            _item(number=4, classification="superseded"),
            _item(number=5, classification="lost"),
        ],
        accepted_classifications=("direct", "indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )

    assert counts.total == 5
    assert counts.accepted == 2
    assert counts.open == 1
    assert counts.superseded == 1
    assert counts.lost == 1
    assert counts.not_shipped == 2
    assert counts.acceptance_rate == 50


def test_format_acceptance_rate_keeps_one_decimal_above_99_without_rounding_to_100() -> None:
    assert format_acceptance_rate((999 / 1000) * 100) == "99.9"
    assert format_acceptance_rate((9999 / 10000) * 100) == "99.9"
    assert format_acceptance_rate(100) == "100"
    assert format_acceptance_rate(99.0) == "99"
    assert format_acceptance_rate(99.04) == "99"
    assert format_acceptance_rate(98.6) == "99"
    assert format_acceptance_rate(None) == "N/A"


def test_report_counts_match_current_generated_pr_data(repo_root: Path) -> None:
    raw_items = load_pr_data_from_html((repo_root / "index.html").read_text(encoding="utf-8"))
    items = report_items_from_script_dicts(raw_items)
    counts = report_counts(
        items,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )

    assert counts.total == sum(1 for item in raw_items if item["classification"] != "withdrawn")
    assert counts.accepted == sum(1 for item in raw_items if item["classification"] in {"shipped", "accepted-indirect"})
    assert counts.open == sum(1 for item in raw_items if item["classification"] == "open")
    assert counts.not_shipped == counts.superseded + counts.lost


def test_default_status_filters_match_ps1_order() -> None:
    counts = report_counts(
        [
            _item(number=1, classification="shipped"),
            _item(number=2, classification="open"),
            _item(number=3, classification="lost"),
            _item(number=4, classification="superseded"),
        ],
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )

    assert default_status_filter_dicts(counts) == [
        {"key": "open", "label": "Open", "count": 1},
        {"key": "shipped", "label": "Shipped", "count": 1},
        {"key": "not-shipped", "label": "Not Shipped", "count": 2},
    ]


def test_report_activity_summary_matches_ps1_active_day_text() -> None:
    summary = report_activity_summary(
        [
            _item(number=1, classification="shipped", createdAt="2026-07-01T00:00:00Z", closedAt="2026-07-02T00:00:00Z"),
            _item(number=2, classification="open", createdAt="2026-07-04T00:00:00Z", closedAt=""),
            _item(number=3, classification="lost", createdAt="2026-07-02T00:00:00Z", closedAt="2026-07-03T00:00:00Z"),
        ],
    )

    assert summary.time_span == "3 days"
    assert summary.time_range == "Active days from Jul 2 - Jul 4"
    assert report_activity_summary([_item(number=1)]).time_span == "N/A"


def test_report_bar_items_match_ps1_width_title_and_content_rules() -> None:
    source = (
        "shipped",
        "shipped",
        "open",
        "lost",
        "superseded",
        "shipped",
        "accepted-indirect",
        "shipped",
    )
    counts = report_counts(
        [_item(number=i, classification=classification) for i, classification in enumerate(source, start=1)],
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )

    assert [(item.key, item.label, item.count, item.width, item.title, item.content) for item in report_bar_items(counts)] == [
        ("shipped", "Shipped", 5, 62.5, "5", "5"),
        ("superseded", "Superseded", 1, 12.5, "", "1"),
        ("lost", "Lost", 1, 12.5, "", "1"),
        ("open", "Open", 1, 12.5, "1", "1"),
    ]


def test_repo_status_rows_match_ps1_rollup_and_details() -> None:
    rows = repo_status_rows(
        [
            _item(number=1, classification="shipped", evidenceKind="direct-merge"),
            _item(number=2, classification="accepted-indirect", evidenceKind="accepted-indirect"),
            _item(number=3, classification="shipped", evidenceKind="timeline"),
            _item(number=4, classification="open"),
            _item(number=5, classification="lost"),
        ],
    )

    assert [(row.label, row.tag_class, row.count, row.details) for row in rows] == [
        ("Shipped", "tag-shipped", 3, "Merged, cherry-picked, and release-credited"),
        ("Open", "tag-open", 1, "Pending review"),
        ("Lost", "tag-lost", 1, "Closed without acceptance"),
    ]


def test_repo_status_rows_keep_zero_shipped_and_open_rows() -> None:
    assert [(row.label, row.count) for row in repo_status_rows([])] == [("Shipped", 0), ("Open", 0)]


def test_report_breakdown_helpers_match_current_generated_index(repo_root: Path) -> None:
    content = (repo_root / "index.html").read_text(encoding="utf-8")
    raw_items = load_pr_data_from_html(content)
    counts = report_counts(
        report_items_from_script_dicts(raw_items),
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )

    assert default_status_filter_dicts(counts) == json.loads(_script_json(content, "PR_FILTERS"))
    assert [(item.key, item.count) for item in report_bar_items(counts)] == [
        ("shipped", counts.accepted),
        ("superseded", counts.superseded),
        ("lost", counts.lost),
        ("open", counts.open),
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


def _script_json(content: str, name: str) -> str:
    match = re.search(rf"var {name} = (.*?);", content, re.S)
    assert match is not None
    return match.group(1)


def _view(**overrides: object) -> GhPullRequestView:
    data: dict[str, object] = {
        "number": 1,
        "state": "CLOSED",
        "title": "Title",
        "createdAt": "2026-07-01T00:00:00Z",
        "closedAt": "",
        "mergedAt": "",
        "author": {"login": "rodboev"},
        "additions": 1,
        "deletions": 2,
        "changedFiles": 3,
        "url": "https://github.com/owner/repo/pull/1",
    }
    data.update(overrides)
    return GhPullRequestView.model_validate(data)


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
        "releaseUrl": "",
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
        releaseUrl=str(data["releaseUrl"]),
        viaLabel=str(data["viaLabel"]),
        viaUrl=str(data["viaUrl"]),
        createdAt=str(data["createdAt"]),
        closedAt=str(data["closedAt"]),
        mergedAt=str(data["mergedAt"]),
        additions=int(data["additions"]),
        deletions=int(data["deletions"]),
        changedFiles=int(data["changedFiles"]),
        evidenceKind=str(data.get("evidenceKind", "")),
    )
