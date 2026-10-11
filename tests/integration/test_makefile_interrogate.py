"""Integration tests for the Makefile's Python docstring-coverage gate.

``make lint`` must reach an ``interrogate`` run that covers every Python file
the repository owns, at a threshold of 100, with a guard that fails on any
``MISSED`` row: interrogate rounds its percentage to one decimal, so the
threshold alone passes a lone gap in a large repository. These tests read the
variables and run the real target against a stub tool, so a deleted flag, a
dropped prerequisite or a guard that stops failing breaks a test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
PRUNED = {".git", ".venv", ".uv-cache", ".uv-tools", "node_modules", "build", "dist"}

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is required")


def _variable(name: str) -> str:
    """Return the value of a ``NAME ?=`` or ``NAME =`` assignment, joined."""
    pattern = re.compile(rf"^{name}\s*\??=\s*((?:.*\\\n)*.*)$", re.MULTILINE)
    match = pattern.search(MAKEFILE.read_text(encoding="utf-8"))
    assert match, f"the Makefile must assign {name}"
    return re.sub(r"\\\n\s*", " ", match.group(1)).strip()


def _prerequisites(target: str) -> list[str]:
    """Return the prerequisites declared for ``target``."""
    pattern = re.compile(rf"^{re.escape(target)}:([^=\n]*)$", re.MULTILINE)
    match = pattern.search(MAKEFILE.read_text(encoding="utf-8"))
    assert match, f"the Makefile must declare a {target} target"
    return match.group(1).split("##")[0].split()


def _python_directories() -> set[str]:
    """Return the paths holding Python source the repository owns."""
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if PRUNED & set(relative.parts):
            continue
        parent = relative.parts[:-1]
        found.add(Path(*parent).as_posix() if parent else relative.name)
    return found


def test_lint_depends_on_interrogate() -> None:
    """Dropping the prerequisite would remove the gate from ``make lint``."""
    assert "interrogate" in _prerequisites("lint")


def test_the_tool_is_pinned_and_run_through_uv() -> None:
    """An unpinned tool could change the verdict with no repository change."""
    assert _variable("INTERROGATE_VERSION") == "1.7.0"
    command = _variable("INTERROGATE")
    assert "uv tool run" in command
    assert "--from 'interrogate==$(INTERROGATE_VERSION)'" in command


def test_the_flags_demand_a_full_exact_report() -> None:
    """Threshold 100, every definition printed, and no ignore flag."""
    flags = _variable("INTERROGATE_FLAGS").split()
    assert flags[flags.index("--fail-under") + 1] == "100"
    assert "-vv" in flags
    assert not [flag for flag in flags if flag.startswith("--ignore")]


def test_every_python_directory_is_a_target() -> None:
    """A Python directory outside ``INTERROGATE_TARGETS`` is not checked."""
    targets = _variable("INTERROGATE_TARGETS").split()
    uncovered = [
        directory
        for directory in sorted(_python_directories())
        if not any(directory == t or directory.startswith(f"{t}/") for t in targets)
    ]
    assert not uncovered, f"add these to INTERROGATE_TARGETS: {uncovered}"


def _run_target(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run ``make interrogate`` with the tool replaced by a stub script.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished ``make`` run, with its output captured.
    """
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/sh\n" + textwrap.dedent(script), encoding="utf-8")
    stub.chmod(0o755)
    return subprocess.run(  # noqa: S603 - fixed argument vector, scratch stub
        ["make", "-C", str(ROOT), "interrogate", f"INTERROGATE={stub}"],  # noqa: S607 - make is resolved from PATH, as the Makefile targets are
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_target_passes_when_the_tool_reports_no_gap(tmp_path: Path) -> None:
    """A clean report and a zero status pass."""
    result = _run_target(tmp_path, "echo '| f.py | COVERED |'\nexit 0\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_target_fails_when_the_tool_fails(tmp_path: Path) -> None:
    """The tool's own non-zero status is kept."""
    result = _run_target(tmp_path, "echo 'FAILED'\nexit 1\n")
    assert result.returncode != 0


def test_the_target_fails_on_a_missed_row_the_percentage_hid(tmp_path: Path) -> None:
    """Rounded to 100.0% with exit 0, a MISSED row still fails the target."""
    result = _run_target(
        tmp_path, "echo '| f.main (L3) |  MISSED |'\necho 'RESULT: PASSED'\nexit 0\n"
    )
    assert result.returncode != 0
    assert "undocumented definitions remain" in result.stderr
