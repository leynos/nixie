"""Integration tests for the Makefile's mdtablefix file selection."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("git") is None,
    reason="make and git are required",
)

_STUB = """#!/bin/sh
# Test double for mdtablefix: --list-files prints Git's Markdown selection and
# every other call records its arguments.
case " $* " in
*" --list-files "*) git ls-files '*.md' ;;
*) printf '%s\\n' "$*" >> "$MDTABLEFIX_LOG" ;;
esac
"""

_FILES = (
    "README.md",
    "docs/guide.md",
    ".rules/rule.md",
    "tests/fixtures/benchmark_docs/proj/doc.md",
    "tests/fixtures/benchmark_sample/sample.md",
)


def _git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` with a throwaway identity."""
    subprocess.run(  # noqa: S603 - fixed argument vector, scratch repository
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _make(repo: Path, target: str, log: Path) -> subprocess.CompletedProcess[str]:
    """Run a Makefile target against the scratch repository with test doubles."""
    stub = repo / "mdtablefix-stub"
    return subprocess.run(  # noqa: S603 - fixed argument vector
        [  # noqa: S607
            "make",
            "-C",
            str(repo),
            "-f",
            str(MAKEFILE),
            target,
            "RUFF=true",
            "MDLINT=true",
            f"MDTABLEFIX={stub}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "MDTABLEFIX_LOG": str(log)},
    )


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """Create a Git repository holding Markdown inside and outside the skip list."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name in _FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Title\n")
    stub = repo / "mdtablefix-stub"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.mark.parametrize("target", ["check-fmt", "fmt"])
def test_mdtablefix_skips_fixture_and_rules_directories(
    scratch_repo: Path, tmp_path: Path, target: str
) -> None:
    """Hand mdtablefix the maintained Markdown only, as the other targets do."""
    log = tmp_path / "calls.log"
    log.write_text("")
    result = _make(scratch_repo, target, log)
    assert result.returncode == 0, result.stderr
    call = log.read_text()
    assert "README.md" in call
    assert "docs/guide.md" in call
    for skipped in (".rules/", "benchmark_docs", "benchmark_sample"):
        assert skipped not in call, f"{target} must not select {skipped}"


def test_fmt_refuses_to_rewrite_while_a_merge_has_conflicts(
    scratch_repo: Path, tmp_path: Path
) -> None:
    """Keep mdtablefix's conflict guard: no in-place run over unmerged paths."""
    readme = scratch_repo / "README.md"
    _git(scratch_repo, "checkout", "-q", "-b", "other")
    readme.write_text("# Other\n")
    _git(scratch_repo, "commit", "-q", "-am", "other")
    _git(scratch_repo, "checkout", "-q", "-")
    readme.write_text("# Mine\n")
    _git(scratch_repo, "commit", "-q", "-am", "mine")
    merge = subprocess.run(
        ["git", "merge", "other"],  # noqa: S607
        cwd=scratch_repo,
        check=False,
        capture_output=True,
    )
    assert merge.returncode != 0, "the fixture must leave a conflicted merge"

    log = tmp_path / "calls.log"
    log.write_text("")
    result = _make(scratch_repo, "fmt", log)
    assert result.returncode != 0
    assert "unresolved merge conflicts" in result.stderr
    assert log.read_text() == "", "mdtablefix must not run over a conflicted tree"
