from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.cache import (
    classification_cache_key,
    dump_cache_json,
    get_cached_closed_classification,
    get_closed_classification_cache_ttl_hours,
    load_cache,
    save_cache,
    set_cached_closed_classification,
)


def test_live_cache_round_trip_is_semantically_lossless(live_cache_path: Path, tmp_path: Path) -> None:
    cache = load_cache(live_cache_path)
    out_path = tmp_path / "round-trip.json"

    save_cache(cache, out_path)
    reloaded = load_cache(out_path)

    assert reloaded == cache
    assert json.loads(out_path.read_text(encoding="utf-8")) == json.loads(dump_cache_json(cache))


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"\xff\xfe\x00\x00",
        b'{"entries": []}',
        ("[" * 512 + "]" * 512).encode("utf-8"),
    ],
)
def test_cache_corruption_returns_usable_empty_cache(
    make_cache_file: object,
    content: bytes,
) -> None:
    path = make_cache_file(content)  # type: ignore[operator]

    cache = load_cache(path)

    assert cache.entries == {}
    assert cache.leaderboards == {}
    assert cache.version == 3


def test_cache_preserves_valid_sections_when_one_section_is_bad(make_cache_file: object) -> None:
    path = make_cache_file(
        json.dumps(
            {
                "version": 3,
                "entries": [],
                "leaderboards": {
                    "repo/name|all": {
                        "cachedAt": "2026-07-01T00:00:00Z",
                        "logins": ["rodboev"],
                        "stats": {},
                    },
                },
            },
        ).encode("utf-8"),
    )  # type: ignore[operator]

    cache = load_cache(path)

    assert cache.entries == {}
    assert "repo/name|all" in cache.leaderboards
    assert cache.section_needs_rebuild("entries")
    assert not cache.section_needs_rebuild("leaderboards")


def test_cache_classification_helpers_round_trip(empty_loaded_cache: object) -> None:
    cache = empty_loaded_cache  # type: ignore[assignment]
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

    set_cached_closed_classification(
        cache,
        repo="owner/repo",
        number=7,
        classification="shipped",
        release="v1.2.3",
        via_label="direct",
        via_url="https://github.com/owner/repo/pull/7",
        evidence_kind="direct-merge",
        now=now,
    )

    entry = get_cached_closed_classification(
        cache,
        repo="owner/repo",
        number=7,
        now=now,
        ttl_hours=24,
    )
    assert entry is not None
    assert cache.entries[classification_cache_key("owner/repo", 7)] == entry
    assert entry.classification == "shipped"
    assert entry.evidenceKind == "direct-merge"


@pytest.mark.parametrize(
    ("closed_at", "classification", "evidence_kind", "expected_hours"),
    [
        ("2026-06-20T00:00:00Z", "shipped", "direct-merge", 24 * 30),
        ("2026-05-01T00:00:00Z", "shipped", "timeline", 24 * 90),
        ("2026-06-20T00:00:00Z", "accepted-indirect", "accepted-indirect", 24 * 7),
        ("2025-01-01T00:00:00Z", "lost", "lost", 24 * 180),
        (None, "unknown", "unknown", 24 * 30),
    ],
)
def test_closed_classification_ttl_profiles(
    closed_at: str | None,
    classification: str,
    evidence_kind: str,
    expected_hours: int,
) -> None:
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

    assert (
        get_closed_classification_cache_ttl_hours(
            closed_at=closed_at,
            classification=classification,
            evidence_kind=evidence_kind,
            now=now,
        )
        == expected_hours
    )
