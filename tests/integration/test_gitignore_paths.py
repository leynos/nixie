"""Integration tests for .gitignore handling with explicit paths."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from nixie.cli import main

if typ.TYPE_CHECKING:
    from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_main_skips_ignored_entries_when_paths_given(
    tmp_path: Path, stub_render: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Respect ``.gitignore`` when explicit directory paths are supplied."""
    keep = tmp_path / "keep.md"
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "skip.md").write_text("ignored")
    keep.write_text("```mermaid\nA-->B\n```")
    (tmp_path / ".gitignore").write_text("ignored/\n")

    monkeypatch.chdir(tmp_path)

    exit_code = await main([Path(".")], 2)  # noqa: PTH201 - explicit current directory
    assert exit_code == 0
    assert stub_render.await_count == 1
    rendered_path = stub_render.await_args_list[0].args[3]  # path argument to renderer
    assert rendered_path == keep.relative_to(tmp_path)
