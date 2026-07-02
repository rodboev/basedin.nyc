from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import re

from pytest import CaptureFixture, MonkeyPatch

import generate

def test_verify_webui_credits_only_uses_python_credit_pipeline(repo_root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "generate.py",
            "--verify-webui-cached-credits-only",
            "--cache-file",
            ".pr-classification-cache.json",
            "--changelog-file",
            "tests/fixtures/hermes-webui-changelog-credit-lines.txt",
            "--contributors-file",
            "tests/fixtures/hermes-webui-CONTRIBUTORS.md",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"franksong2702: \d+ \(expected >= 200\) OK", result.stdout)
    assert re.search(r"Michaelyklam: \d+ \(expected >= 100\) OK", result.stdout)
    assert re.search(r"rodboev: \d+ \(expected >= 150\) OK", result.stdout)
    assert re.search(r"ai-ag2026: \d+ \(expected >= 80\) OK", result.stdout)
    assert re.search(r"rodboev/franksong2702: 0\.\d{2} \(expected >= 0\.50\) OK", result.stdout)
    assert re.search(r"Michaelyklam/franksong2702: 0\.\d{2} \(expected >= 0\.35\) OK", result.stdout)
    assert "FAIL" not in result.stdout

def test_verify_webui_credits_only_rejects_stale_live_changelog(
    repo_root: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(generate, "run_gh", lambda *_args: "different-changelog-sha")

    result = generate.verify_webui_credits_only(
        cache_file=repo_root / ".pr-classification-cache.json",
        changelog_file=None,
        contributors_file=repo_root / "tests/fixtures/hermes-webui-CONTRIBUTORS.md",
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "release credit cache for nesquena/hermes-webui is stale" in captured.err
    assert "rebuild is not implemented in Python yet" in captured.err
