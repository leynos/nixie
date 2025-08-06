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
    monkeypatch.setattr(sys, "argv", ["nixie", "--verbose", "file.md"])
    parsed = parse_args()
    assert parsed.verbose is True
    assert parsed.paths == [Path("file.md")]


@pytest.mark.asyncio
async def test_render_block_emits_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        proc: object, path: Path, idx: int, timeout: float
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
async def test_render_block_silent_without_verbose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        proc: object, path: Path, idx: int, timeout: float
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
