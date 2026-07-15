from __future__ import annotations

from core.timeline import TimelineDay, aggregate_daily, breakdown_seed, prepare_timeline_prs, slice_daily


def _pr(
    *,
    number: int,
    classification: str,
    created: str,
    resolved: str = "",
) -> dict[str, object]:
    return {
        "repo": "owner/repo",
        "number": number,
        "classification": classification,
        "createdAt": created,
        "closedAt": resolved,
        "mergedAt": "",
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
        "loc": loc,
        "clsShipped": shipped,
        "clsOpen": open_,
        "clsSuperseded": sup,
        "clsLost": lost,
    }


def test_slice_daily_keeps_the_tail_from_the_cutoff_on_and_treats_zero_as_every_day() -> None:
    days = [_series_day("2026-07-01"), _series_day("2026-07-13"), _series_day("2026-07-14"), _series_day("2026-07-15")]

    # sliceData() cuts at last-days and keeps `>=`, so a 1-day window spans two dates.
    assert [day["date"] for day in slice_daily(days, 1)] == ["2026-07-14", "2026-07-15"]
    assert [day["date"] for day in slice_daily(days, 0)] == [day["date"] for day in days]


def test_breakdown_seed_reads_the_one_day_window_not_the_all_time_totals() -> None:
    days = [
        _series_day("2026-02-05", opened=400, loc=900_000, shipped=380, lost=15, sup=5),
        _series_day("2026-07-14", opened=12, loc=10_000, shipped=20, open_=4),
        _series_day("2026-07-15", opened=13, loc=20_000, shipped=4, open_=16),
    ]

    seed = breakdown_seed(days, "2026-07-15")

    assert (seed.counts.total, seed.counts.accepted, seed.counts.open) == (44, 24, 20)
    assert (seed.counts.superseded, seed.counts.lost, seed.counts.not_shipped) == (0, 0, 0)
    assert seed.counts.acceptance_rate == 100
    assert seed.avg_prs == "13"  # 25 opened / 2 active days, rounded half up
    assert seed.avg_loc == "15.0k"
    # Today is partial, so it is excluded from the count while still bounding the label.
    assert seed.activity.time_span == "1 day"
    assert seed.activity.time_range == "Active days from Jul 14 - Jul 15"


def test_breakdown_seed_reports_a_zero_rate_when_the_window_closed_nothing() -> None:
    days = [_series_day("2026-07-14", opened=5, loc=100), _series_day("2026-07-15", opened=7, loc=200, open_=7)]

    seed = breakdown_seed(days, "2026-07-15")

    # bdDisplay() coerces the null 0/0 rate to 0; the seed has to agree or the first frame jumps.
    assert seed.counts.acceptance_rate == 0
    assert (seed.counts.total, seed.counts.accepted) == (7, 0)
