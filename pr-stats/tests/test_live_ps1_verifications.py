from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


LIVE_VERIFY_SCRIPTS = (
    "verify-author-withdrawals.ps1",
    "verify-credited-superseded.ps1",
    "verify-maintainer-superseded.ps1",
    "verify-webui-release-credits.ps1",
)


@pytest.mark.live
@pytest.mark.parametrize("script_name", LIVE_VERIFY_SCRIPTS)
def test_live_ps1_verification_script(script_name: str, repo_root: Path) -> None:
    script = repo_root / "tests" / script_name
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )

    assert result.returncode == 0, f"{script_name} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
