from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
from pathlib import Path

import pytest

from nixie.cli import _run_mermaid_cli, get_mmdc_cmd


@pytest.mark.asyncio
async def test_run_mermaid_cli_invokes_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    mmd = tmp_path / "diagram.mmd"
    mmd.write_text("A-->B")
    svg = mmd.with_suffix(".svg")
    semaphore = asyncio.Semaphore(1)
    path = Path("doc.md")

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        proc: object, path: Path, idx: int, timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    with caplog.at_level(logging.INFO, logger="nixie.cli"):
        await _run_mermaid_cli(mmd, svg, cfg_path, semaphore, path, 1, 30.0)

    expected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert expected in caplog.text


@pytest.mark.asyncio
async def test_run_mermaid_cli_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    mmd = tmp_path / "diagram.mmd"
    mmd.write_text("A-->B")
    svg = mmd.with_suffix(".svg")
    semaphore = asyncio.Semaphore(1)
    path = Path("doc.md")

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        proc: object, path: Path, idx: int, timeout: float
    ) -> tuple[bool, bytes]:
        return False, b"Parse error on line 1:\nfoo\n^\n"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    with pytest.raises(RuntimeError) as err:
        await _run_mermaid_cli(mmd, svg, cfg_path, semaphore, path, 1, 30.0)

    assert "Parse error on line 1" in str(err.value)
