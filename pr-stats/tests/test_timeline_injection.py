from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def test_timeline_injection_is_idempotent(repo_root: Path) -> None:
    module = _load_generate_timeline(repo_root)
    html = """<html><head></head><body>
<div>
<div class="legend">Legend</div>
</div>
<h2>repo</h2>
<h2>Methodology</h2>
</body></html>
"""

    once = module.inject_into_index(html, "[]", "{}", "[]", "0", "0")
    twice = module.inject_into_index(once, "[]", "{}", "[]", "0", "0")

    assert twice == once
    assert "\n\n\n<!-- timeline-chart -->" not in twice
    assert twice.count(module.CHART_MARKER) == 2


def _load_generate_timeline(repo_root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_timeline", repo_root / "generate-timeline.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
