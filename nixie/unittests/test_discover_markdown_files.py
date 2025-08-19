"""Tests for Markdown file discovery helpers."""

from __future__ import annotations

import typing as typ

import pytest

from nixie.cli import collect_markdown_files, discover_markdown_files

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def cwd_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run tests from a temporary working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_discover_markdown_files_respects_gitignore(cwd_tmp: Path) -> None:
    """Skip directories listed in ``.gitignore`` when searching for Markdown."""
    keep = cwd_tmp / "keep.md"
    ignored_dir = cwd_tmp / "ignored"
    ignored_dir.mkdir()
    skip = ignored_dir / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (cwd_tmp / ".gitignore").write_text("ignored/\n")

    found = list(discover_markdown_files())
    assert found == [keep]


def test_discover_markdown_files_skips_root_file(cwd_tmp: Path) -> None:
    """Skip files ignored at the repository root."""
    keep = cwd_tmp / "keep.md"
    skip = cwd_tmp / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (cwd_tmp / ".gitignore").write_text("skip.md\n")

    found = list(discover_markdown_files())
    assert found == [keep]


def test_discover_markdown_files_handles_negation_and_order(cwd_tmp: Path) -> None:
    """Re-include negated patterns and yield results sorted by path (deterministic).

    Deterministic ordering keeps assertions stable.
    """
    ignored_dir = cwd_tmp / "ignored"
    ignored_dir.mkdir()
    reincluded = ignored_dir / "keep.md"
    reincluded.write_text("keep")
    skipped = ignored_dir / "skip.md"
    skipped.write_text("skip")
    root = cwd_tmp / "root.md"
    root.write_text("root")
    (cwd_tmp / ".gitignore").write_text("ignored/\n!ignored/keep.md\n")

    found = list(discover_markdown_files())
    assert found == [reincluded, root]


def test_discover_markdown_files_empty_directory(cwd_tmp: Path) -> None:
    """Return no paths when the directory has no Markdown files."""
    (cwd_tmp / ".gitignore").write_text("\n")

    found = list(discover_markdown_files())
    assert found == []


def test_discover_markdown_files_ignores_nested_gitignore(cwd_tmp: Path) -> None:
    """Nested ``.gitignore`` files are ignored."""
    sub = cwd_tmp / "sub"
    sub.mkdir()
    keep = sub / "keep.md"
    skip = sub / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (sub / ".gitignore").write_text("skip.md\n")

    found = list(discover_markdown_files())
    assert set(found) == {keep, skip}


def test_collect_markdown_files_respects_gitignore(cwd_tmp: Path) -> None:
    """Skip ignored paths when expanding explicit directories."""
    keep = cwd_tmp / "keep.md"
    ignored_dir = cwd_tmp / "ignored"
    ignored_dir.mkdir()
    skip = ignored_dir / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (cwd_tmp / ".gitignore").write_text("ignored/\n")

    found = list(collect_markdown_files([cwd_tmp]))
    assert found == [keep]
