"""Tests for rendering diagrams via the CLI helpers."""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
from pathlib import Path

import pytest

from nixie.cli import _render_diagram, _run_mermaid_cli, get_mmdc_cmd


@pytest.mark.asyncio
async def test_render_diagram_writes_file_and_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Write diagram to disk and log the CLI invocation."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = Path("doc.md")
    block = "A-->B"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        assert semaphore.locked()
        return True, b""

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    with caplog.at_level(logging.INFO, logger="nixie.cli"):
        await _render_diagram(block, tmp_path, cfg_path, path, 1, semaphore, 30.0)

    mmd = tmp_path / "doc_1.mmd"
    assert mmd.read_text() == block
    svg = mmd.with_suffix(".svg")
    expected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert expected in caplog.text


@pytest.mark.asyncio
async def test_render_diagram_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Raise ``RuntimeError`` when the CLI reports failure."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    semaphore = asyncio.Semaphore(1)
    path = Path("doc.md")
    block = "A-->B"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        assert semaphore.locked()
        return False, b"Parse error on line 1:\nfoo\n^\n"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/mmdc")

    with pytest.raises(RuntimeError) as err:
        await _render_diagram(block, tmp_path, cfg_path, path, 1, semaphore, 30.0)

    msg = str(err.value)
    assert "Parse error on line 1" in msg
    assert "doc.md" in msg
    assert "mmdc" in msg


@pytest.mark.asyncio
async def test_run_mermaid_cli_rejects_unexpected_executable() -> None:
    """Reject executables outside the allowed set."""
    semaphore = asyncio.Semaphore(1)
    path = Path("doc.md")
    cmd = ["echo", "hello"]
    with pytest.raises(ValueError, match="Unexpected executable"):
        await _run_mermaid_cli(cmd, semaphore, path, 1, 30.0)
