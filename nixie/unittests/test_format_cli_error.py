"""Snapshot tests pinning nixie's user-visible error formatting.

The formatted error text is part of nixie's output contract: CI logs and
editors surface it verbatim, so it must not drift silently. Each backend has
a distinct stderr shape — mmdc emits ``Parse error on line N:`` blocks while
merman-cli prints its ``CliError`` display directly — and these snapshots pin
how nixie renders both.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from nixie.cli import ResolvedRenderer, _render_diagram, format_cli_error

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

MMDC_PARSE_ERROR_STDERR: typ.Final[str] = (
    "UnknownDiagramError: Parse error on line 2:\n"
    "graph TD; A--oops\n"
    "----------^\n"
    "Expecting 'SEMI', 'NEWLINE', got 'NODE_STRING'\n"
    "    at new UnknownDiagramError (file:///mermaid/errors.js:10:5)\n"
)

MERMAN_STDERR: typ.Final[str] = (
    "Mermaid error: unknown diagram type at line 1\nhelp: see merman parse\n"
)


def test_format_cli_error_extracts_mmdc_parse_error(
    snapshot: SnapshotAssertion,
) -> None:
    """Extract the concise parse-error block from mmdc stderr."""
    assert format_cli_error(MMDC_PARSE_ERROR_STDERR) == snapshot


def test_format_cli_error_passes_merman_stderr_through(
    snapshot: SnapshotAssertion,
) -> None:
    """Fall back to stripped stderr for merman-style error output."""
    assert format_cli_error(MERMAN_STDERR) == snapshot


def test_format_cli_error_handles_truncated_parse_error(
    snapshot: SnapshotAssertion,
) -> None:
    """Fall back to raw stderr when the parse-error block is incomplete."""
    truncated = "Parse error on line 7:\n"
    assert format_cli_error(truncated) == snapshot


async def _capture_render_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    renderer: ResolvedRenderer,
    stderr: bytes,
    which_name: str,
    which_path: str,
    cfg_path: Path | None,
) -> str:
    """Run ``_render_diagram`` against a failing stub and return the message.

    Temporary directory paths vary per run, so they are normalised to
    ``<tmpdir>`` to keep the snapshot stable.
    """

    async def fake_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> object:
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return False, stderr

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr(
        "nixie.cli.shutil.which",
        lambda cmd: which_path if cmd == which_name else None,
    )

    with pytest.raises(RuntimeError) as err:
        await _render_diagram(
            "A-->B", tmp_path, cfg_path, Path("doc.md"), 1, 30.0, renderer=renderer
        )
    return str(err.value).replace(str(tmp_path), "<tmpdir>")


@pytest.mark.asyncio
async def test_render_diagram_error_shape_mmdc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_home_cwd: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Pin the full RuntimeError message shape for the mmdc backend."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    message = await _capture_render_failure(
        monkeypatch,
        tmp_path,
        renderer=ResolvedRenderer(backend="mmdc", needs_puppeteer_config=True),
        stderr=MMDC_PARSE_ERROR_STDERR.encode(),
        which_name="mmdc",
        which_path="/usr/bin/mmdc",
        cfg_path=cfg_path,
    )
    assert message == snapshot


@pytest.mark.asyncio
async def test_render_diagram_error_shape_merman(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_home_cwd: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Pin the full RuntimeError message shape for the merman backend."""
    message = await _capture_render_failure(
        monkeypatch,
        tmp_path,
        renderer=ResolvedRenderer(backend="merman", needs_puppeteer_config=False),
        stderr=MERMAN_STDERR.encode(),
        which_name="merman-cli",
        which_path="/usr/local/bin/merman-cli",
        cfg_path=None,
    )
    assert message == snapshot
