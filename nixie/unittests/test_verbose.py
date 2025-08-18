"""Tests for verbose logging behavior."""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from nixie.cli import get_mmdc_cmd, parse_args, render_block


def test_parse_args_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse the ``--verbose`` flag into the argument namespace."""
    monkeypatch.setattr(sys, "argv", ["nixie", "--verbose", "file.md"])
    parsed = parse_args()
    assert parsed.verbose is True
    assert parsed.paths == [Path("file.md")]


@pytest.mark.asyncio
async def test_render_block_emits_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fake_home_cwd: Path,
) -> None:
    """Log the CLI command when verbose logging is enabled."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    block = "A-->B"
    with caplog.at_level(logging.INFO, logger="nixie.cli"):
        assert await render_block(block, tmp_path, cfg_path, path, 1, semaphore)

    mmd = tmp_path / "doc_1.mmd"
    svg = mmd.with_suffix(".svg")
    expected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert expected in caplog.text


@pytest.mark.asyncio
async def test_render_block_verbose_deprecated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fake_home_cwd: Path,
) -> None:
    """Test deprecated verbose parameter still works but warns."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    block = "A-->B"
    logging.getLogger("nixie.cli").setLevel(logging.WARNING)
    with (
        pytest.warns(
            DeprecationWarning,
            match=(
                r"The 'verbose' parameter is deprecated; configure the"
                r" 'nixie\.cli' logger level instead\."
            ),
        ),
        caplog.at_level(logging.WARNING, logger="nixie.cli"),
    ):
        assert await render_block(
            block,
            tmp_path,
            cfg_path,
            path,
            1,
            semaphore,
            verbose=True,
        )

    mmd = tmp_path / "doc_1.mmd"
    svg = mmd.with_suffix(".svg")
    unexpected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert unexpected not in caplog.text


@pytest.mark.asyncio
async def test_render_block_silent_without_verbose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fake_home_cwd: Path,
) -> None:
    """Avoid emitting command when only warnings are logged."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    block = "A-->B"
    with caplog.at_level(logging.WARNING, logger="nixie.cli"):
        assert await render_block(block, tmp_path, cfg_path, path, 1, semaphore)

    mmd = tmp_path / "doc_1.mmd"
    svg = mmd.with_suffix(".svg")
    unexpected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert unexpected not in caplog.text


@pytest.mark.asyncio
async def test_render_block_logs_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fake_home_cwd: Path,
) -> None:
    """Log error when CLI tool is missing."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", _cmd[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    block = "A-->B"
    with caplog.at_level(logging.ERROR, logger="nixie.cli"):
        result = await render_block(block, tmp_path, cfg_path, path, 1, semaphore)

    assert result is False
    assert "not found" in caplog.text


@pytest.mark.asyncio
async def test_render_block_logs_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log runtime errors during diagram rendering."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def raise_runtime_error(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("nixie.cli._render_diagram", raise_runtime_error)

    block = "A-->B"
    with caplog.at_level(logging.ERROR, logger="nixie.cli"):
        result = await render_block(block, tmp_path, cfg_path, path, 1, semaphore)

    assert result is False
    assert "Runtime error while rendering diagram" in caplog.text


@pytest.mark.asyncio
async def test_render_block_logs_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log unexpected exceptions during diagram rendering."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    class BoomError(Exception):
        """Uh oh."""

    async def raise_exception(*_args: object, **_kwargs: object) -> None:
        raise BoomError

    monkeypatch.setattr("nixie.cli._render_diagram", raise_exception)

    block = "A-->B"
    with caplog.at_level(logging.ERROR, logger="nixie.cli"):
        result = await render_block(block, tmp_path, cfg_path, path, 1, semaphore)

    assert result is False
    assert "unexpected error in diagram" in caplog.text
