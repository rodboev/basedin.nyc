from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


def _read_index(repo_root: Path) -> str:
    return (repo_root / "index.html").read_text(encoding="utf-8")


def _pr_data(content: str) -> list[dict[str, Any]]:
    match = re.search(r"var PR_DATA = (\[.*?\]);", content, re.S)
    assert match is not None, "Could not find PR_DATA in index.html."
    data = json.loads(match.group(1))
    assert isinstance(data, list)
    return data


def _single_pr(items: list[dict[str, Any]], number: int) -> dict[str, Any]:
    matches = [item for item in items if item.get("number") == number]
    assert len(matches) == 1, f"Expected exactly one PR_DATA entry for #{number}, found {len(matches)}."
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
        r'<div class="stat-card"><div class="number green"[^>]*>(\d+)%</div><div class="label"[^>]*>Acceptance(?: rate)? \((\d+) superseded, (\d+) lost\)</div></div>',
        content,
    )

    assert match is not None
    rate = int(match.group(1))
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


def test_shipped_counts_are_rolled_up_consistently(repo_root: Path) -> None:
    content = _read_index(repo_root)
    shipped = _stat_number(content, " green", "Shipped")
    pill = re.search(r'<div class="sort-pill active" data-status="shipped">Shipped \((\d+)\)</div>', content)

    assert pill is not None
    assert int(pill.group(1)) == shipped
    assert 'data-status="accepted-indirect"' not in content
    assert '"key":"accepted-indirect"' not in content
    assert '"statusKey":"accepted-indirect"' not in content


def test_repo_filter_pills_and_client_filtering_are_present(repo_root: Path) -> None:
    content = _read_index(repo_root)

    assert 'id="pr-repo-pills"' in content
    assert 'data-repo="all">All</div>' in content
    assert 'data-repo="webui">webui</div>' in content
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


def test_representative_prs_are_inlined_and_curated(repo_root: Path) -> None:
    content = _read_index(repo_root)
    representative = re.search(r"<h2>Representative PRs</h2>\s*<table[^>]*>.*?</table>", content, re.S)

    assert representative is not None
    block = representative.group(0)
    assert 'id="representative-prs"' not in content
    assert "loadRepresentativePrs()" not in content
    assert "fetch(src, { credentials: 'same-origin' })" not in content
    assert "https://github.com/nesquena/hermes-webui/pull/3571" in block
    assert "https://github.com/kenn-io/agentsview/pull/734" in block
    assert "https://github.com/kenn-io/agentsview/pull/733" in block
    assert "https://github.com/NousResearch/hermes-agent/pull/" not in block
    assert "https://github.com/nesquena/hermes-webui/pull/3606" not in block
    assert "https://github.com/nesquena/hermes-webui/pull/3667" not in block


def test_specific_pr_classifications_from_generated_data(repo_root: Path) -> None:
    items = _pr_data(_read_index(repo_root))

    if not any(item.get("number") in {39391, 40144} for item in items):
        pytest.skip("Legacy hermes-agent PR fixtures are outside the current generated report window.")
    assert _single_pr(items, 39391)["statusKey"] == "lost"
    assert _single_pr(items, 40144)["statusKey"] == "withdrawn"
    assert _single_pr(items, 3563)["statusKey"] == "superseded"

    optional_1085 = [item for item in items if item.get("number") == 1085]
    assert len(optional_1085) <= 1
    if optional_1085:
        assert optional_1085[0]["statusKey"] == "superseded"


def test_claude_mem_indirect_landings_roll_up_under_shipped(repo_root: Path) -> None:
    items = _pr_data(_read_index(repo_root))

    for number in (2848, 2850, 2851, 2852):
        pr = _single_pr(items, number)
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
