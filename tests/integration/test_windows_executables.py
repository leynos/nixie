"""Behavioural tests covering Windows mermaid-cli executables."""

from __future__ import annotations

from pathlib import Path

import pytest

from nixie.cli import render_block


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executable",
    [
        r"C:\Users\runneradmin\.bun\bin\mmdc.EXE",
        r"C:\Users\runneradmin\.bun\bin\mmdc.CMD",
        r"C:\Users\runneradmin\.bun\bin\mmdc.BAT",
    ],
)
async def test_render_block_accepts_windows_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executable: str
) -> None:
    """Allow Windows shims discovered via ``which`` when rendering."""

    async def fake_create_subprocess_exec(*cmd: str, **_kwargs: object) -> object:
        assert cmd[0] == executable
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr("nixie.cli.Path.home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_which(cmd: str) -> str | None:
        return executable if cmd == "mmdc" else None

    monkeypatch.setattr("nixie.cli.shutil.which", fake_which)

    result = await render_block("A-->B", tmp_path, None, Path("doc.md"), 1)
    assert result is True


@pytest.mark.asyncio
async def test_render_block_rejects_unexpected_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Surface a clear error when a non-allowlisted executable is discovered."""

    async def fail_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        # ``ty`` cannot see through pytest's ``_WithException`` protocol
        # wrapper, so it rejects valid ``skip``/``fail`` call signatures.
        pytest.fail(reason="create_subprocess_exec should not be called")  # ty: ignore[unknown-argument]

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fail_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.Path.home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/python" if cmd == "mmdc" else None

    monkeypatch.setattr("nixie.cli.shutil.which", fake_which)

    result = await render_block("A-->B", tmp_path, None, Path("doc.md"), 1)
    assert result is False
