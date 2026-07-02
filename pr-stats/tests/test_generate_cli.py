from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import re

from pytest import CaptureFixture, MonkeyPatch

import generate
from core.html import normalize_generated_html

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

def test_inject_timeline_only_writes_requested_output(repo_root: Path, tmp_path: Path) -> None:
    out_file = tmp_path / "python-index.html"

    result = subprocess.run(
        [
            sys.executable,
            "generate.py",
            "--inject-timeline-only",
            "--in-file",
            "index.html",
            "--out-file",
            str(out_file),
            "--repos-file",
            "generate.ps1",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    content = out_file.read_text(encoding="utf-8")
    assert content.count("<!-- timeline-chart -->") == 2
    assert "var TL_ALL = " in content


def test_inject_timeline_only_is_normalized_html_parity(repo_root: Path, tmp_path: Path) -> None:
    out_file = tmp_path / "python-index.html"
    source = (repo_root / "index.html").read_text(encoding="utf-8")

    result = generate.inject_timeline_only(
        in_file=repo_root / "index.html",
        out_file=out_file,
        repos_file=repo_root / "generate.ps1",
    )

    assert result == 0
    assert normalize_generated_html(out_file.read_text(encoding="utf-8")) == normalize_generated_html(source)

def test_default_generate_writes_complete_report_with_normalized_parity(repo_root: Path, tmp_path: Path) -> None:
    out_file = tmp_path / "python-index.html"
    source = (repo_root / "index.html").read_text(encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "generate.py",
            "--in-file",
            "index.html",
            "--out-file",
            str(out_file),
            "--cache-file",
            ".pr-classification-cache.json",
            "--repos-file",
            "generate.ps1",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    content = out_file.read_text(encoding="utf-8")
    assert "var PR_DATA = " in content
    assert "var TL_ALL = " in content
    assert normalize_generated_html(content) == normalize_generated_html(source)

def test_default_generate_sanity_gate_keeps_existing_output(repo_root: Path, tmp_path: Path) -> None:
    in_file = tmp_path / "broken.html"
    out_file = tmp_path / "index.html"
    in_file.write_text("no report data", encoding="utf-8")
    out_file.write_text('<div class="number">30</div><div class="label">Total PRs</div>', encoding="utf-8")

    result = generate.generate_report(
        cache_file=repo_root / ".pr-classification-cache.json",
        in_file=in_file,
        out_file=out_file,
        repos_file=repo_root / "generate.ps1",
    )

    assert result == 1
    assert out_file.read_text(encoding="utf-8") == '<div class="number">30</div><div class="label">Total PRs</div>'
