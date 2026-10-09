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
# Test double for mdtablefix. --list-files prints Git's Markdown selection
# (tracked and untracked, ignoring what Git ignores), recording its flags; every
# other call records one argument per line, then a separator, and fails when
# MDTABLEFIX_FAIL is set.
case " $* " in
*" --list-files "*)
  printf 'LIST %s\\n' "$*" >> "$MDTABLEFIX_LOG"
  git ls-files --cached --others --exclude-standard '*.md' ;;
*)
  printf 'CALL\\n' >> "$MDTABLEFIX_LOG"
  for arg in "$@"; do printf 'ARG %s\\n' "$arg" >> "$MDTABLEFIX_LOG"; done
  [ -z "${MDTABLEFIX_FAIL:-}" ] || exit 3 ;;
esac
"""

_FILES = (
    "README.md",
    "docs/guide.md",
    ".rules/rule.md",
    "tests/fixtures/benchmark_docs/proj/doc.md",
    "tests/fixtures/benchmark_sample/sample.md",
    "docs/with space.md",
)


def _git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` with a throwaway identity."""
    subprocess.run(  # noqa: S603 - fixed argument vector, scratch repository
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _make(
    repo: Path, target: str, log: Path, *, fail: bool = False
) -> subprocess.CompletedProcess[str]:
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
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "MDTABLEFIX_LOG": str(log),
            **({"MDTABLEFIX_FAIL": "1"} if fail else {}),
        },
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
    # An untracked document that Git does not ignore must still be formatted,
    # and so must one inside a skipped directory be left alone.
    (repo / "docs" / "new.md").write_text("# New\n")
    (repo / "tests" / "fixtures" / "benchmark_docs" / "untracked.md").write_text(
        "# U\n"
    )
    return repo


def _calls(log: Path) -> list[list[str]]:
    """Return the argument vectors of the non-listing mdtablefix calls."""
    calls: list[list[str]] = []
    for line in log.read_text().splitlines():
        if line == "CALL":
            calls.append([])
        elif line.startswith("ARG "):
            calls[-1].append(line.removeprefix("ARG "))
    return calls


@pytest.mark.parametrize(
    ("target", "mode"), [("check-fmt", "--check"), ("fmt", "--in-place")]
)
def test_mdtablefix_gets_the_maintained_markdown_with_the_target_flags(
    scratch_repo: Path, tmp_path: Path, target: str, mode: str
) -> None:
    """Hand mdtablefix the maintained Markdown only, one argument per path."""
    log = tmp_path / "calls.log"
    log.write_text("")
    result = _make(scratch_repo, target, log)
    assert result.returncode == 0, result.stderr

    listing = [line for line in log.read_text().splitlines() if line.startswith("LIST")]
    assert listing == ["LIST --list-files --git --include-untracked"], listing

    (call,) = _calls(log)
    assert mode in call, f"{target} must pass {mode}: {call}"
    for flag in ("--wrap", "--renumber", "--breaks", "--ellipsis", "--fences"):
        assert flag in call, f"{target} must pass {flag}: {call}"
    paths = [arg for arg in call if arg.endswith(".md")]
    assert sorted(paths) == [
        "README.md",
        "docs/guide.md",
        "docs/new.md",
        "docs/with space.md",
    ], f"{target} selected {paths}"
    for skipped in (".rules/", "benchmark_docs", "benchmark_sample"):
        assert not any(skipped in arg for arg in call), (
            f"{target} must not select {skipped}"
        )


@pytest.mark.parametrize("target", ["check-fmt", "fmt"])
def test_a_formatter_failure_fails_the_target(
    scratch_repo: Path, tmp_path: Path, target: str
) -> None:
    """Propagate mdtablefix's exit status rather than masking it in the pipe."""
    log = tmp_path / "calls.log"
    log.write_text("")
    result = _make(scratch_repo, target, log, fail=True)
    assert result.returncode != 0, f"{target} swallowed a formatter failure"


@pytest.mark.parametrize("target", ["markdownlint", "spelling", "nixie"])
def test_the_other_markdown_targets_share_the_exclusions(
    scratch_repo: Path, target: str
) -> None:
    """Build the git pathspecs of the other targets from the same variable."""
    result = subprocess.run(  # noqa: S603 - fixed argument vector
        [  # noqa: S607
            "make",
            "-n",
            "MAKE=true",
            "-C",
            str(scratch_repo),
            "-f",
            str(MAKEFILE),
            target,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, result.stderr
    for skipped in (".rules", "benchmark_docs", "benchmark_sample"):
        assert (
            f"':!{skipped}" in result.stdout
            or f"':!tests/fixtures/{skipped}" in result.stdout
        ), f"{target} must skip {skipped}: {result.stdout}"


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
    assert _calls(log) == [], "mdtablefix must not run over a conflicted tree"
