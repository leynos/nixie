"""Tests for ``discover_markdown_files``."""

from __future__ import annotations

import typing as typ

from nixie.cli import discover_markdown_files

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
