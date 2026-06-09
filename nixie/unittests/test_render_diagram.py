"""Tests for rendering diagrams via the CLI helpers."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

import pytest

from nixie.cli import (
    WINDOWS_EXECUTABLE_SUFFIXES,
    ResolvedRenderer,
    _render_diagram,
    _run_mermaid_cli,
    get_mmdc_cmd,
)


@pytest.mark.asyncio
async def test_render_diagram_writes_file_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fake_home_cwd: Path,
) -> None:
    """Write diagram to disk and log the CLI invocation."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    path = Path("doc.md")
    block = "A-->B"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(
        "nixie.cli.shutil.which",
        lambda cmd: "/usr/bin/mmdc" if cmd == "mmdc" else None,
    )

    with caplog.at_level(logging.INFO, logger="nixie.cli"):
        await _render_diagram(block, tmp_path, cfg_path, path, 1, 30.0)

    mmd = tmp_path / "doc_1.mmd"
    assert mmd.read_text() == block
    svg = mmd.with_suffix(".svg")
    expected = shlex.join(get_mmdc_cmd(mmd, svg, cfg_path))
    assert expected in caplog.text


@pytest.mark.asyncio
async def test_render_diagram_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_home_cwd: Path,
) -> None:
    """Raise ``RuntimeError`` when the CLI reports failure."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    path = Path("doc.md")
    block = "A-->B"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return False, b"Parse error on line 1:\nfoo\n^\n"

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(
        "nixie.cli.shutil.which",
        lambda cmd: "/usr/bin/mmdc" if cmd == "mmdc" else None,
    )

    with pytest.raises(RuntimeError) as err:
        await _render_diagram(block, tmp_path, cfg_path, path, 1, 30.0)

    msg = str(err.value)
    assert "Parse error on line 1" in msg
    assert "doc.md" in msg
    assert "mmdc" in msg


@pytest.mark.asyncio
async def test_render_diagram_merman_failure_reports_raw_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_home_cwd: Path,
) -> None:
    """Surface merman-cli stderr verbatim when rendering fails."""
    path = Path("doc.md")
    block = "A-->B"

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return False, b"Mermaid error: unknown diagram type\n"

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(
        "nixie.cli.shutil.which",
        lambda cmd: "/usr/local/bin/merman-cli" if cmd == "merman-cli" else None,
    )
    renderer = ResolvedRenderer(backend="merman", needs_puppeteer_config=False)

    with pytest.raises(RuntimeError) as err:
        await _render_diagram(block, tmp_path, None, path, 1, 30.0, renderer=renderer)

    msg = str(err.value)
    assert "merman-cli" in msg
    assert "doc.md" in msg
    assert "Mermaid error: unknown diagram type" in msg
    assert "--puppeteerConfigFile" not in msg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executable",
    ["", "python", "./mmdc.sh", r"C:\tools\mermaid.cmdx"],
)
async def test_run_mermaid_cli_rejects_unexpected_executable(
    executable: str,
) -> None:
    """Reject executables outside the allowed set."""
    path = Path("doc.md")
    cmd = [executable] if executable else []
    with pytest.raises(ValueError, match="Unexpected executable"):
        await _run_mermaid_cli(cmd, path, 1, 30.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    WINDOWS_EXECUTABLE_SUFFIXES,
)
async def test_run_mermaid_cli_accepts_windows_executable(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """Treat Windows executables with common suffixes as valid shims."""

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)

    cmd = [rf"C:\Users\runneradmin\.bun\bin\mmdc{suffix.upper()}", "--version"]
    result = await _run_mermaid_cli(cmd, Path("doc.md"), 1, 30.0)
    assert result == (True, b"")
