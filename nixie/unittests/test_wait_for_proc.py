"""Tests for :func:`nixie.cli.wait_for_proc`."""

from __future__ import annotations

import asyncio
import asyncio.subprocess as asyncio_subprocess
import sys
import typing as typ

import pytest

from nixie.cli import wait_for_proc

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_wait_for_proc_handles_asyncio_timeout_error(tmp_path: Path) -> None:
    """Handle :class:`asyncio.TimeoutError` raised by ``asyncio.wait_for``."""
    cmd = [sys.executable, "-c", "import time; time.sleep(1)"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio_subprocess.PIPE,
        stderr=asyncio_subprocess.PIPE,
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(proc.communicate(), 0.01)
    proc.kill()
    await proc.wait()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio_subprocess.PIPE,
        stderr=asyncio_subprocess.PIPE,
    )
    success, stderr = await wait_for_proc(proc, tmp_path / "dummy.md", 1, timeout=0.01)
    assert not success
    assert b"diagram 1 timed out" in stderr
