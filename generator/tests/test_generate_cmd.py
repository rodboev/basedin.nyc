from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command wrapper")
@pytest.mark.parametrize(
    ("key", "fail", "expected"),
    [
        ("P", "", ["add", "commit", "push", "watch"]),
        ("p", "", ["add", "commit", "push", "watch"]),
        ("P", "push", ["add", "commit", "push"]),
        ("\r", "", []),
        ("\x03", "", []),
        ("interrupt", "", []),
        ("P", "generate", []),
        ("P", "add", ["add"]),
        ("P", "commit", ["add", "commit"]),
    ],
)
def test_publish_requires_p_and_successful_commands(tmp_path: Path, key: str, fail: str, expected: list[str]) -> None:
    wrapper = Path(__file__).resolve().parents[1] / "generate.cmd"
    script = wrapper.read_text().replace("python ", f'"{sys.executable}" ').replace("git ", f'"{sys.executable}" fake_git.py ').replace("import msvcrt, sys", "import fake_msvcrt as msvcrt, sys")
    (tmp_path / "generate.cmd").write_text(script)
    (tmp_path / "generate.py").write_text("import os, sys; sys.exit(1 if os.environ['FAIL'] == 'generate' else 0)\n")
    (tmp_path / "watch_deploy.py").write_text("open('git.log', 'a').write('watch\\n')\n")
    (tmp_path / "fake_msvcrt.py").write_text(
        "import os, sys\n"
        "sys.stdin.isatty = lambda: True\n"
        "def getwch():\n"
        "    if os.environ['KEY'] == 'interrupt':\n"
        "        raise KeyboardInterrupt\n"
        "    return os.environ['KEY']\n"
    )
    (tmp_path / "fake_git.py").write_text(
        "import os, sys\n"
        "with open('git.log', 'a') as log:\n"
        "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(1 if os.environ['FAIL'] == sys.argv[1] else 0)\n"
    )
    subprocess.run(
        ["cmd.exe", "/d", "/c", str(tmp_path / "generate.cmd")],
        cwd=tmp_path.parent,
        env={**os.environ, "KEY": key, "FAIL": fail},
        capture_output=True,
        timeout=20,
    )
    log = tmp_path / "git.log"
    commands = log.read_text().splitlines() if log.exists() else []
    assert [command.split()[0] for command in commands] == expected
    if "push" in expected:
        assert commands[:3] == ["add -- ../index.html .pr-classification-cache.json", "commit --amend --no-edit", "push --force"]
