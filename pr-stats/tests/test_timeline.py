from __future__ import annotations

import pytest

from core.timeline import TimelineDay, aggregate_daily, breakdown_seed, prepare_timeline_prs, slice_daily


def _pr(
    *,
    number: int,
    classification: str,
    created: str,
    resolved: str = "",
    merged: str = "",
) -> dict[str, object]:
    return {
        "repo": "owner/repo",
        "number": number,
        "classification": classification,
        "createdAt": created,
        "closedAt": resolved,
        "mergedAt": merged,
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
    }


def _day(days: list[TimelineDay], date: str) -> TimelineDay | None:
    return next((day for day in days if day["date"] == date), None)


def test_outcomes_land_on_the_day_they_resolved_not_the_day_the_pr_opened() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([
            _pr(number=1, classification="lost", created="2026-06-09T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
            _pr(number=2, classification="superseded", created="2026-06-10T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
        ]),
    )

    opened_june = _day(days, "2026-06-09")
    assert opened_june is not None
    assert opened_june["prsOpened"] == 1
    # The June day carries the opened PR but none of its outcome.
    assert (opened_june["clsLost"], opened_june["clsSuperseded"]) == (0, 0)

    resolved_july = _day(days, "2026-07-13")
    assert resolved_july is not None
    assert resolved_july["prsOpened"] == 0
    assert (resolved_july["clsLost"], resolved_july["clsSuperseded"]) == (1, 1)


def test_a_merge_lands_on_the_merge_day_even_when_github_stamps_closed_a_second_later() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([
            # 6:56 PM ET on the 13th vs 12:00 AM ET on the 14th: the two fields pick different days.
            _pr(
                number=1,
                classification="shipped",
                created="2026-06-01T12:00:00Z",
                merged="2026-07-13T22:56:51Z",
                resolved="2026-07-14T04:00:00Z",
            ),
        ]),
    )

    merge_day = _day(days, "2026-07-13")
    assert merge_day is not None
    assert (merge_day["clsShipped"], merge_day["prsShipped"]) == (1, 1)
    assert _day(days, "2026-07-14") is None


def test_a_shipped_pr_with_no_resolve_date_keeps_both_shipped_series_on_one_day() -> None:
    # No mergedAt and no closedAt is unreachable on live data, but the two series used to disagree
    # about it: the class count fell back to the opened day while prsShipped counted nowhere.
    days = aggregate_daily(
        prepare_timeline_prs([_pr(number=1, classification="shipped", created="2026-06-01T12:00:00Z")]),
    )

    opened = _day(days, "2026-06-01")
    assert opened is not None
    assert (opened["clsShipped"], opened["prsShipped"]) == (1, 1)
    assert [day["date"] for day in days] == ["2026-06-01"]


def test_open_prs_stay_on_their_opened_day_since_they_have_no_outcome() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([_pr(number=1, classification="open", created="2026-07-14T12:00:00Z")]),
    )

    opened = _day(days, "2026-07-14")
    assert opened is not None
    assert (opened["prsOpened"], opened["clsOpen"]) == (1, 1)


def test_reclassifying_by_outcome_date_preserves_all_time_totals() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([
            _pr(number=1, classification="shipped", created="2026-06-01T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
            _pr(number=2, classification="lost", created="2026-06-02T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
            _pr(number=3, classification="open", created="2026-07-14T12:00:00Z"),
            _pr(number=4, classification="withdrawn", created="2026-06-03T12:00:00Z", resolved="2026-06-04T12:00:00Z"),
        ]),
    )

    totals = {key: sum(int(day[key]) for day in days) for key in ("clsShipped", "clsOpen", "clsSuperseded", "clsLost")}
    # Re-bucketing moves outcomes between days; it must not create or drop any. Withdrawn stays out.
    assert totals == {"clsShipped": 1, "clsOpen": 1, "clsSuperseded": 0, "clsLost": 1}
    assert sum(int(day["prsOpened"]) for day in days) == 3


def _series_day(date: str, *, opened: int = 0, loc: int = 0, shipped: int = 0, open_: int = 0, sup: int = 0, lost: int = 0) -> TimelineDay:
    return {
        "date": date,
        "prsOpened": opened,
        "prsShipped": shipped,
        "loc": loc,
        "clsShipped": shipped,
        "clsOpen": open_,
        "clsSuperseded": sup,
        "clsLost": lost,
    }


def test_slice_daily_keeps_the_tail_from_the_cutoff_on_and_treats_zero_as_every_day() -> None:
    days = [_series_day("2026-07-01"), _series_day("2026-07-13"), _series_day("2026-07-14"), _series_day("2026-07-15")]

    # sliceData() cuts at last-days and keeps `>=`, so an N-day window spans N+1 dates.
    assert [day["date"] for day in slice_daily(days, 2)] == ["2026-07-13", "2026-07-14", "2026-07-15"]
    assert [day["date"] for day in slice_daily(days, 0)] == [day["date"] for day in days]


def _seed_fixture() -> list[TimelineDay]:
    # Jul 10 lands inside BD_RATE_SEED_RANGE but outside BD_LOAD_SEED_RANGE, so the two windows
    # disagree on the rate and the override is actually exercised.
    return [
        _series_day("2026-02-05", opened=400, loc=900_000, shipped=380, lost=15, sup=5),
        _series_day("2026-07-10", opened=4, loc=1_000, shipped=20),
        _series_day("2026-07-13", opened=5, loc=5_000, shipped=6, lost=2, sup=1),
        _series_day("2026-07-14", opened=12, loc=10_000, shipped=20, open_=4),
        _series_day("2026-07-15", opened=13, loc=20_000, shipped=4, open_=16),
    ]


def test_breakdown_seed_reads_the_seed_window_not_the_all_time_totals() -> None:
    seed = breakdown_seed(_seed_fixture(), "2026-07-15")

    assert (seed.counts.total, seed.counts.accepted, seed.counts.open) == (53, 30, 20)
    assert (seed.counts.superseded, seed.counts.lost, seed.counts.not_shipped) == (1, 2, 3)
    assert seed.avg_prs == "10"  # 30 opened / 3 active days
    assert seed.avg_loc == "11.7k"
    # Today is in progress, so updateBreakdown() drops it from the tally and rolls the range end
    # back to the previous active day. Diverge here and bd-days-label snaps on the final frame.
    assert seed.activity.time_span == "2 days"
    assert seed.activity.time_range == "Active days from Jul 13 - Jul 14"


def test_breakdown_seed_takes_only_its_rate_from_the_wider_window() -> None:
    seed = breakdown_seed(_seed_fixture(), "2026-07-15")

    # Mirrors `states[0].rate = states[1].rate`: the counts stay on the 2d window (30 shipped of 33
    # closed = 90.9%) while the rate reads the 7d window, which pulls Jul 10's 20 shipped in.
    assert seed.counts.acceptance_rate == pytest.approx(50 / 53 * 100)
    assert seed.counts.acceptance_rate != pytest.approx(30 / 33 * 100)


def test_breakdown_seed_has_no_range_when_today_is_the_only_active_day() -> None:
    days = [_series_day("2026-07-14"), _series_day("2026-07-15", opened=7, loc=200, open_=7)]

    seed = breakdown_seed(days, "2026-07-15")

    assert seed.activity.time_span == "0 days"
    assert seed.activity.time_range == "No active days in range"


def test_breakdown_seed_reports_a_zero_rate_when_the_window_closed_nothing() -> None:
    days = [_series_day("2026-07-14", opened=5, loc=100), _series_day("2026-07-15", opened=7, loc=200, open_=7)]

    seed = breakdown_seed(days, "2026-07-15")

    # bdDisplay() coerces the null 0/0 rate to 0; the seed has to agree or the first frame jumps.
    assert seed.counts.acceptance_rate == 0
    assert (seed.counts.total, seed.counts.accepted) == (7, 0)


def test_merged_loc_counts_to_the_day_it_merged_not_the_day_it_opened() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([
            _pr(
                number=1,
                classification="shipped",
                created="2026-06-01T12:00:00Z",
                merged="2026-07-13T12:00:00Z",
                resolved="2026-07-13T12:00:00Z",
            ),
        ]),
    )

    opened_june = _day(days, "2026-06-01")
    assert opened_june is not None
    assert opened_june["prsOpened"] == 1
    # June got the PR; the merged code belongs to July.
    assert opened_june["loc"] == 0

    merged_july = _day(days, "2026-07-13")
    assert merged_july is not None
    assert (merged_july["prsShipped"], merged_july["loc"]) == (1, 1)


def test_open_prs_contribute_no_loc_until_they_land() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([_pr(number=1, classification="open", created="2026-07-14T12:00:00Z")]),
    )

    opened = _day(days, "2026-07-14")
    assert opened is not None
    assert (opened["prsOpened"], opened["clsOpen"], opened["loc"]) == (1, 1, 0)


def test_non_shipped_outcomes_contribute_no_loc() -> None:
    days = aggregate_daily(
        prepare_timeline_prs([
            _pr(number=1, classification="lost", created="2026-06-01T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
            _pr(number=2, classification="superseded", created="2026-06-02T12:00:00Z", resolved="2026-07-13T12:00:00Z"),
        ]),
    )

    resolved_july = _day(days, "2026-07-13")
    assert resolved_july is not None
    assert (resolved_july["clsLost"], resolved_july["clsSuperseded"]) == (1, 1)
    assert resolved_july["loc"] == 0
