from __future__ import annotations

from core.timeline import build_chart_payload


def test_build_chart_payload_rounds_avg_prs_to_nearest_integer() -> None:
    _, _, _, avg_prs, _ = build_chart_payload(
        [
            {"date": "2026-07-01", "prsOpened": 12, "loc": 10},
            {"date": "2026-07-02", "prsOpened": 13, "loc": 20},
        ],
        {},
        [],
    )

    assert avg_prs == "13"
