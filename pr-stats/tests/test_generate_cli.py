from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

import generate

def test_verify_webui_credits_only_uses_python_credit_pipeline(repo_root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "generate.py",
            "--verify-webui-credits-only",
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

    assert result.returncode == 1
    assert "franksong2702:" in result.stdout
    assert "Michaelyklam:" in result.stdout
    assert "rodboev:" in result.stdout
    assert "ai-ag2026:" in result.stdout
    assert "Michaelyklam: 152 (expected 115-140) FAIL" in result.stdout
    assert "rodboev: 208 (expected 115-135) FAIL" in result.stdout

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
