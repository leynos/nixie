from __future__ import annotations

import asyncio
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from nixie.cli import get_mmdc_cmd, parse_args, render_block


def test_parse_args_verbose(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["nixie", "--verbose", "file.md"])
    parsed = parse_args()
    assert parsed.verbose is True
    assert parsed.paths == [Path("file.md")]


@pytest.mark.asyncio
async def test_render_block_emits_command(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = tmp_path / "doc.md"

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return object()

    async def fake_wait_for_proc(proc, path, idx, timeout):
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/mmdc")

    block = "A-->B"
    assert await render_block(
        block, tmp_path, cfg_path, path, 1, semaphore, verbose=True
    )

    out, _ = capsys.readouterr()
    mmd = tmp_path / "doc_1.mmd"
    svg = mmd.with_suffix(".svg")
    expected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert expected in out
