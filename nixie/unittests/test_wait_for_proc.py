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
async def test_wait_for_proc_times_out(tmp_path: Path) -> None:
    """Return ``False`` when the process exceeds ``timeout``."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(1)",
        stdout=asyncio_subprocess.PIPE,
        stderr=asyncio_subprocess.PIPE,
    )
    success, stderr = await wait_for_proc(proc, tmp_path / "dummy.md", 1, timeout=0.01)
    assert not success
    assert stderr == b""
