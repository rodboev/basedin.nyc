from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from core.timeline import (
    breakdown_seed,
    build_daily_data,
    load_active_repos_from_text,
    load_pr_data_from_html,
    prepare_timeline_prs,
)


CLAUDE_MEM = "thedotmack/claude-mem"
HERMES_AGENT = "NousResearch/hermes-agent"
HERMES_WEBUI = "nesquena/hermes-webui"


def _read_index(repo_root: Path) -> str:
    return (repo_root / "index.html").read_text(encoding="utf-8")


def _pr_data(content: str) -> list[dict[str, Any]]:
    return load_pr_data_from_html(content)


def _script_array(content: str, name: str) -> Any:
    match = re.search(rf"var {name} = (.*?);", content, re.S)
    assert match is not None, f"Could not find {name} in index.html."
    return json.loads(match.group(1))


def _prs_in_repo(items: list[dict[str, Any]], repo: str, number: int) -> list[dict[str, Any]]:
    # PR numbers restart per repo, so a bare number matches across repos: agentsview
    # and claude-mem both have a #1085, and eleven other numbers collide today.
    return [item for item in items if item.get("number") == number and item.get("repo") == repo]


def _single_pr(items: list[dict[str, Any]], repo: str, number: int) -> dict[str, Any]:
    matches = _prs_in_repo(items, repo, number)
    assert len(matches) == 1, f"Expected exactly one PR_DATA entry for {repo}#{number}, found {len(matches)}."
    return matches[0]


def _stat_number(content: str, class_name: str, label: str) -> int:
    match = re.search(
        rf'<div class="stat-card"><div class="number{class_name}"[^>]*>(\d+)</div><div class="label">{re.escape(label)}</div></div>',
        content,
    )
    assert match is not None, f"Could not find {label} stat card."
    return int(match.group(1))


def test_acceptance_rate_card_uses_shortened_not_shipped_wording(repo_root: Path) -> None:
    content = _read_index(repo_root)
    match = re.search(
        r'<div class="stat-card"><div class="number green"[^>]*>(\d+(?:\.\d)?)%</div><div class="label"[^>]*>Acceptance(?: rate)? \((\d+) superseded, (\d+) lost\)</div></div>',
        content,
    )

    assert match is not None
    rate = float(match.group(1))
    superseded = int(match.group(2))
    lost = int(match.group(3))
    assert superseded >= 0
    assert lost >= 0
    assert 0 <= rate <= 100


def test_summary_cards_add_up_to_total(repo_root: Path) -> None:
    content = _read_index(repo_root)

    total = _stat_number(content, "", "Total PRs")
    shipped = _stat_number(content, " green", "Shipped")
    open_count = _stat_number(content, " yellow", "Open")
    lost_withdrawn = _stat_number(content, "", "Lost/Superseded")

    assert shipped + open_count + lost_withdrawn == total


def test_breakdown_cards_render_the_load_seed_not_the_all_time_rollup(repo_root: Path) -> None:
    content = _read_index(repo_root)
    today = re.search(r"var TL_TODAY = '([^']+)'", content)
    assert today is not None
    seed = breakdown_seed(_script_array(content, "TL_ALL"), today.group(1))

    # timeline.js lerps out of bdDisplay(bdStats(BD_LOAD_RANGES[0])). The markup has to already sit
    # on that frame; any drift here is a visible jump the instant the animation takes the first frame.
    assert _stat_number(content, "", "Total PRs") == seed.counts.total
    assert _stat_number(content, " green", "Shipped") == seed.counts.accepted
    assert _stat_number(content, " yellow", "Open") == seed.counts.open
    assert _stat_number(content, "", "Lost/Superseded") == seed.counts.not_shipped


def test_bar_segments_carry_their_width_inline(repo_root: Path) -> None:
    content = _read_index(repo_root)

    # Flex items with no width collapse to their labels, so a width applied by a later script leaves
    # the bar a sliver for the whole first paint.
    assert "data-width" not in content
    for key in ("shipped", "superseded", "lost", "open"):
        assert re.search(rf'id="bd-bar-{key}" style="width:[\d.]+%"', content) is not None


def test_shipped_counts_are_rolled_up_consistently(repo_root: Path) -> None:
    content = _read_index(repo_root)
    pill = re.search(r'<div class="sort-pill active" data-status="shipped">Shipped \((\d+)\)</div>', content)

    # The PR table filter stays on the all-time rollup; only the breakdown carries the seed.
    assert pill is not None
    assert int(pill.group(1)) == sum(int(day["clsShipped"]) for day in _script_array(content, "TL_ALL"))
    assert 'data-status="accepted-indirect"' not in content
    assert '"key":"accepted-indirect"' not in content
    assert '"statusKey":"accepted-indirect"' not in content


def test_repo_filter_pills_and_client_filtering_are_present(repo_root: Path) -> None:
    content = _read_index(repo_root)

    assert 'id="pr-repo-pills"' in content
    assert 'data-repo="all">All</div>' in content
    assert 'data-repo="hermes-webui">hermes-webui</div>' in content
    assert 'data-repo="agentsview">agentsview</div>' in content
    assert 'data-repo="claude-mem">claude-mem</div>' in content
    assert re.search(r'data-repo="[^"]+">[^<]*\(\d+\)</div>', content) is None
    assert re.search(r"var CURRENT_PR_FILTER = \{\s*statusKey: 'shipped',\s*repoKey: 'all'\s*\};", content) is not None
    assert re.search(r'data-status="open">Open \(\d+\)</div>', content) is not None
    assert re.search(r'data-status="shipped">Shipped \(\d+\)</div>', content) is not None
    assert re.search(r'data-status="not-shipped">Not Shipped \(\d+\)</div>', content) is not None
    assert "function updatePrFilterPills" in content
    assert "updatePrFilterPills();" in content
    assert "renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);" in content
    assert "bindPillGroup('pr-repo-pills', 'data-repo', 'repoKey');" in content
    assert "statusKey === 'not-shipped'" in content


def test_timeline_repo_labels_use_repo_names(repo_root: Path) -> None:
    content = (repo_root / "timeline.js").read_text(encoding="utf-8")

    assert "var TL_LABELS" not in content
    assert "pill.textContent = name;" in content


def test_specific_pr_classifications_from_generated_data(repo_root: Path) -> None:
    items = _pr_data(_read_index(repo_root))

    if not _prs_in_repo(items, HERMES_AGENT, 39391) and not _prs_in_repo(items, HERMES_AGENT, 40144):
        pytest.skip("Legacy hermes-agent PR fixtures are outside the current generated report window.")
    assert _single_pr(items, HERMES_AGENT, 39391)["statusKey"] == "lost"
    assert _single_pr(items, HERMES_AGENT, 40144)["statusKey"] == "withdrawn"
    assert _single_pr(items, HERMES_WEBUI, 3563)["statusKey"] == "superseded"

    optional_1085 = _prs_in_repo(items, CLAUDE_MEM, 1085)
    assert len(optional_1085) <= 1
    if optional_1085:
        assert optional_1085[0]["statusKey"] == "superseded"


def test_pr_data_is_keyed_by_repo_and_number_not_number_alone(repo_root: Path) -> None:
    """The invariant every _single_pr lookup rests on.

    A bare number is not a key: agentsview#1085 (shipped) and claude-mem#1085
    (superseded) coexist, and pinning a classification by number alone matched both.
    """
    items = _pr_data(_read_index(repo_root))
    keys = [(item.get("repo"), item.get("number")) for item in items]

    assert len(keys) == len(set(keys))


def test_claude_mem_indirect_landings_roll_up_under_shipped(repo_root: Path) -> None:
    items = _pr_data(_read_index(repo_root))

    for number in (2848, 2850, 2851, 2852):
        pr = _single_pr(items, CLAUDE_MEM, number)
        assert pr["statusKey"] == "shipped"
        assert pr["releaseLabel"] == "indirect"
        assert pr["viaLabel"] == "#2862"


def test_pr_data_keeps_timeline_fields_and_timeline_is_injected(repo_root: Path) -> None:
    content = _read_index(repo_root)
    items = _pr_data(content)
    assert items
    required = {"repo", "classification", "createdAt", "closedAt", "mergedAt", "additions", "deletions", "changedFiles"}

    missing_by_number = {
        item.get("number"): sorted(required - item.keys())
        for item in items
        if required - item.keys()
    }
    assert missing_by_number == {}
    assert "var TL_ALL = " in content


def test_timeline_aggregate_matches_injected_tl_all(repo_root: Path) -> None:
    content = _read_index(repo_root)
    repos = load_active_repos_from_text((repo_root / "repos.txt").read_text(encoding="utf-8"))
    aggregate, repo_data, repo_names = build_daily_data(prepare_timeline_prs(load_pr_data_from_html(content)), repos)

    assert aggregate == _script_array(content, "TL_ALL")
    assert repo_data == _script_array(content, "TL_REPOS")
    assert repo_names == _script_array(content, "TL_NAMES")
