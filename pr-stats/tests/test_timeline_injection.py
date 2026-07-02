from __future__ import annotations

from pathlib import Path

from core.timeline import CHART_MARKER, inject_timeline_chart


def test_timeline_injection_is_idempotent() -> None:
    html = """<html><head></head><body>
<div>
<div class="legend">Legend</div>
</div>
<h2>repo</h2>
<h2>Methodology</h2>
</body></html>
"""

    once = inject_timeline_chart(html, "[]", "{}", "[]", "0", "0", today="2026-07-02")
    twice = inject_timeline_chart(once, "[]", "{}", "[]", "0", "0", today="2026-07-02")

    assert twice == once
    assert "\n\n\n<!-- timeline-chart -->" not in twice
    assert twice.count(CHART_MARKER) == 2


def test_ps1_outfile_is_passed_to_timeline_injector(repo_root: Path) -> None:
    script = (repo_root / "generate.ps1").read_text(encoding="utf-8")

    assert "python $timelinePy --in-file $OutFile --out-file $OutFile --repos-file $PSCommandPath" in script
