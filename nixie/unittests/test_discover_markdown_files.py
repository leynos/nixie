"""Tests for Markdown file discovery helpers."""

from __future__ import annotations

import typing as typ

from nixie.cli import collect_markdown_files, discover_markdown_files

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_discover_markdown_files_respects_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip directories listed in ``.gitignore`` when searching for Markdown."""
    keep = tmp_path / "keep.md"
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    skip = ignored_dir / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (tmp_path / ".gitignore").write_text("ignored/\n")

    monkeypatch.chdir(tmp_path)

    found = list(discover_markdown_files())
    assert found == [keep]


def test_discover_markdown_files_skips_root_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip files ignored at the repository root."""
    keep = tmp_path / "keep.md"
    skip = tmp_path / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (tmp_path / ".gitignore").write_text("skip.md\n")

    monkeypatch.chdir(tmp_path)

    found = list(discover_markdown_files())
    assert found == [keep]


def test_discover_markdown_files_handles_negation_and_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Support re-inclusion patterns and yield deterministically."""
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    reincluded = ignored_dir / "keep.md"
    reincluded.write_text("keep")
    skipped = ignored_dir / "skip.md"
    skipped.write_text("skip")
    root = tmp_path / "root.md"
    root.write_text("root")
    (tmp_path / ".gitignore").write_text("ignored/\n!ignored/keep.md\n")

    monkeypatch.chdir(tmp_path)

    found = list(discover_markdown_files())
    assert found == [reincluded, root]


def test_discover_markdown_files_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return no paths when the directory has no Markdown files."""
    (tmp_path / ".gitignore").write_text("\n")
    monkeypatch.chdir(tmp_path)

    found = list(discover_markdown_files())
    assert found == []


def test_discover_markdown_files_ignores_nested_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested ``.gitignore`` files are ignored."""
    sub = tmp_path / "sub"
    sub.mkdir()
    keep = sub / "keep.md"
    skip = sub / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (sub / ".gitignore").write_text("skip.md\n")

    monkeypatch.chdir(tmp_path)

    found = list(discover_markdown_files())
    assert set(found) == {keep, skip}


def test_collect_markdown_files_respects_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip ignored paths when expanding explicit directories."""
    keep = tmp_path / "keep.md"
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    skip = ignored_dir / "skip.md"
    keep.write_text("ok")
    skip.write_text("nope")
    (tmp_path / ".gitignore").write_text("ignored/\n")

    monkeypatch.chdir(tmp_path)

    found = list(collect_markdown_files([tmp_path]))
    assert found == [keep]
