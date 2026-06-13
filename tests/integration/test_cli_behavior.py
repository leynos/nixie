"""Integration tests for the CLI's high-level behaviour."""

from __future__ import annotations

import asyncio
import sys
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from nixie.cli import (
    ASCII_SUCCESS_BANNER,
    SUCCESS_BANNER,
    UNKNOWN_SCHEMA,
    main,
    resolve_success_banner,
)

if typ.TYPE_CHECKING:
    from unittest.mock import AsyncMock


class SimulatedProcessingError(ValueError):
    """Exception used to simulate processing failures in tests."""

    def __init__(self) -> None:
        super().__init__("Simulated processing error")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "structure",
        "inputs",
        "expected_exit",
        "error_substring",
        "expected_calls",
    ),
    [
        (
            {"good.md": "```mermaid\nA-->B\n```"},
            ["good.md"],
            0,
            None,
            [("good.md", 1)],
        ),
        (
            {"bad.md": "```mermaid\nINVALID\n```"},
            ["bad.md"],
            1,
            "Parse error",
            [("bad.md", 1)],
        ),
        (
            {
                "docs/one.md": "```mermaid\nA-->B\n```",
                "docs/two.md": "```mermaid\ninvalid diagram\n```",
                "extra.md": "Just text",
            },
            ["docs", "extra.md"],
            1,
            "Parse error",
            [("docs/one.md", 1), ("docs/two.md", 1)],
        ),
        (
            {"multi.md": ("```mermaid\nA-->B\n```\n\n```mermaid\ninvalid\n```")},
            ["multi.md"],
            1,
            "Parse error",
            [("multi.md", 1), ("multi.md", 2)],
        ),
        (
            {"none.md": "No diagrams here"},
            ["none.md"],
            0,
            None,
            [],
        ),
    ],
)
async def test_cli_behavior(
    tmp_path: Path,
    stub_render: AsyncMock,
    capsys: pytest.CaptureFixture[str],
    structure: dict[str, str],
    inputs: list[str],
    expected_exit: int,
    error_substring: str | None,
    expected_calls: list[tuple[str, int]],
) -> None:
    """Process various input structures and validate outcomes."""
    for rel, content in structure.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    paths = [tmp_path / p for p in inputs]
    exit_code = await main(paths)
    captured = capsys.readouterr()
    assert exit_code == expected_exit
    if error_substring is None:
        assert captured.err == ""
    else:
        assert error_substring in captured.err

    assert stub_render.await_count == len(expected_calls)
    actual = {
        (call.args[3].relative_to(tmp_path), call.args[4])
        for call in stub_render.await_args_list
    }
    expected = {(Path(p), i) for p, i in expected_calls}
    assert actual == expected

    if expected_exit == 0:
        assert any(
            captured.out.count(banner) == 1
            for banner in (SUCCESS_BANNER, ASCII_SUCCESS_BANNER)
        )
    else:
        assert captured.out.count(SUCCESS_BANNER) == 0
        assert captured.out.count(ASCII_SUCCESS_BANNER) == 0


@pytest.mark.asyncio
async def test_cli_marks_file_boundaries(
    tmp_path: Path, stub_render: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Emit clear boundary markers for each processed file."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("```mermaid\nA-->B\n```")
    file_b.write_text("No diagrams here")

    exit_code = await main([file_a, file_b])
    captured = capsys.readouterr()

    assert exit_code == 0
    lines = captured.out.splitlines()
    markers = [
        f"==> {file_a}",
        f"<== {file_a}",
        f"==> {file_b}",
        f"<== {file_b}",
    ]
    for marker in markers:
        assert lines.count(marker) == 1
    positions = [lines.index(marker) for marker in markers]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_cli_reports_diagram_schemas(
    tmp_path: Path, stub_render: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show schema names and line numbers for each diagram."""
    file = tmp_path / "a.md"
    file.write_text(
        "\n".join(
            [
                "preamble",
                "```mermaid",
                "sequenceDiagram",
                "A->B",
                "```",
                "",
                "```mermaid",
                "classDiagram",
                "A--|>B",
                "```",
            ]
        )
    )

    exit_code = await main([file])
    captured = capsys.readouterr()

    assert exit_code == 0, "CLI should succeed for valid diagrams"
    lines = captured.out.splitlines()
    markers = [
        "--> line 3: sequenceDiagram",
        "<-- line 5: sequenceDiagram",
        "--> line 8: classDiagram",
        "<-- line 10: classDiagram",
    ]
    for marker in markers:
        assert lines.count(marker) == 1, f"Expected exactly one '{marker}'"
    positions = [lines.index(marker) for marker in markers]
    assert positions == sorted(positions), "Markers must appear in order"


@pytest.mark.asyncio
async def test_cli_reports_unknown_schema(
    tmp_path: Path, stub_render: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report ``UNKNOWN_SCHEMA`` when no schema token is found."""
    file = tmp_path / "unknown.md"
    file.write_text(
        "\n".join(
            [
                "```mermaid",
                "   ",  # blank
                "%% comment",  # comment line
                "A-->B",  # no explicit schema token on first meaningful line
                "```",
            ]
        )
    )

    exit_code = await main([file])
    captured = capsys.readouterr()

    assert exit_code == 0, "CLI should succeed for structurally valid diagram"
    out = captured.out
    lines = out.splitlines()
    start_markers = [line for line in lines if line.startswith("-->")]
    end_markers = [line for line in lines if line.startswith("<--")]
    assert len(start_markers) == 1, "Expected one start marker"
    assert len(end_markers) == 1, "Expected one end marker"
    assert UNKNOWN_SCHEMA in out, (
        f"Expected {UNKNOWN_SCHEMA} when no schema token is found"
    )


@pytest.mark.asyncio
async def test_cli_passes_mermaid_version(
    tmp_path: Path,
    stub_render: AsyncMock,
) -> None:
    """Forward the requested mermaid-cli version to the renderer."""
    file = tmp_path / "diagram.md"
    file.write_text("```mermaid\nA-->B\n```")

    exit_code = await main([file], mermaid_version="10.9.1")

    assert exit_code == 0
    assert stub_render.await_count == 1
    await_args = stub_render.await_args
    assert await_args is not None
    assert await_args.kwargs["mermaid_version"] == "10.9.1"


@pytest.mark.asyncio
async def test_cli_handles_file_processing_error(
    tmp_path: Path,
    stub_render: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Handle file preparation failures without halting processing."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("```mermaid\nA-->B\n```")
    file_b.write_text("TRIGGER_PARSE_BLOCKS_ERROR")

    from nixie import cli as cli_module

    original_parse_blocks = cli_module.parse_blocks

    def mock_parse_blocks(text: str) -> list[cli_module.Diagram]:
        if "TRIGGER_PARSE_BLOCKS_ERROR" in text:
            raise SimulatedProcessingError
        return original_parse_blocks(text)

    monkeypatch.setattr(cli_module, "parse_blocks", mock_parse_blocks)

    exit_code = await main([file_a, file_b])
    captured = capsys.readouterr()

    assert exit_code == 1
    lines = captured.out.splitlines()
    markers = [
        f"==> {file_a}",
        f"<== {file_a}",
        f"==> {file_b}",
        f"<== {file_b}",
    ]
    for marker in markers:
        assert lines.count(marker) == 1
    positions = [lines.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "Simulated processing error" in captured.out
    # Markers should bracket the failing diagram as well.
    start_markers = [line for line in lines if line.startswith("--> line ")]
    end_markers = [line for line in lines if line.startswith("<-- line ")]
    assert len(start_markers) == 1, "Expected one start marker despite the failure"
    assert len(end_markers) == 1, "Expected one end marker despite the failure"


@pytest.mark.asyncio
async def test_cli_preserves_deterministic_order_with_out_of_order_completions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit markers in stable order even when tasks complete out of order."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text(
        "\n".join(
            [
                "```mermaid",
                "sequenceDiagram %% slowA",
                "A->B",
                "```",
                "",
                "```mermaid",
                "classDiagram %% fastA",
                "A--|>B",
                "```",
            ]
        )
    )
    file_b.write_text(
        "\n".join(
            [
                "```mermaid",
                "flowchart %% fastB",
                "A-->B",
                "```",
            ]
        )
    )
    completion_order: list[str] = []

    async def fake_render(
        block: str,
        _tmpdir: Path,
        _cfg_path: Path | None,
        _path: Path,
        _idx: int,
        timeout: float,
        mermaid_version: str = "latest",
        *,
        renderer: object | None = None,
    ) -> None:
        _ = renderer
        _ = timeout
        _ = mermaid_version
        if "slowA" in block:
            await asyncio.sleep(0.2)
            completion_order.append("a1")
            return
        if "fastA" in block:
            await asyncio.sleep(0.01)
            completion_order.append("a2")
            return
        await asyncio.sleep(0.02)
        completion_order.append("b1")

    monkeypatch.setattr("nixie.cli._render_diagram", fake_render)

    exit_code = await main([file_a, file_b], max_concurrency=3)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert completion_order == ["a2", "b1", "a1"]
    lines = captured.out.splitlines()
    expected_markers = [
        f"==> {file_a}",
        "--> line 2: sequenceDiagram",
        "<-- line 4: sequenceDiagram",
        "--> line 7: classDiagram",
        "<-- line 9: classDiagram",
        f"<== {file_a}",
        f"==> {file_b}",
        "--> line 2: flowchart",
        "<-- line 4: flowchart",
        f"<== {file_b}",
    ]
    positions = [lines.index(marker) for marker in expected_markers]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_cli_caps_concurrency_to_cpu_count_minus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never run more than ``cpu_count - 1`` diagram checks concurrently."""
    file = tmp_path / "many.md"
    file.write_text(
        "\n\n".join(
            [
                "```mermaid\nsequenceDiagram %% d1\nA->B\n```",
                "```mermaid\nsequenceDiagram %% d2\nA->B\n```",
                "```mermaid\nsequenceDiagram %% d3\nA->B\n```",
                "```mermaid\nsequenceDiagram %% d4\nA->B\n```",
            ]
        )
    )
    active = 0
    peak_active = 0

    async def fake_render(
        _block: str,
        _tmpdir: Path,
        _cfg_path: Path | None,
        _path: Path,
        _idx: int,
        timeout: float,
        mermaid_version: str = "latest",
        *,
        renderer: object | None = None,
    ) -> None:
        _ = renderer
        nonlocal active, peak_active
        _ = timeout
        _ = mermaid_version
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.05)
        active -= 1

    monkeypatch.setattr("nixie.cli._render_diagram", fake_render)
    monkeypatch.setattr("nixie.cli.os.cpu_count", lambda: 3)

    exit_code = await main([file], max_concurrency=99)

    assert exit_code == 0
    assert peak_active <= 2


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (SimpleNamespace(encoding="utf-8"), SUCCESS_BANNER),
        (SimpleNamespace(encoding="cp1252"), ASCII_SUCCESS_BANNER),
        (SimpleNamespace(encoding=None), SUCCESS_BANNER),
        (None, SUCCESS_BANNER),
        (SimpleNamespace(encoding="not-an-encoding"), ASCII_SUCCESS_BANNER),
    ],
)
def test_resolve_success_banner_handles_non_utf8_streams(
    stream: SimpleNamespace | None, expected: str
) -> None:
    """Prefer the celebratory banner but fall back when encoding rejects it."""
    assert resolve_success_banner(stream) == expected


class _RecordingStream:
    """In-memory text stream that exposes an ``encoding`` attribute."""

    def __init__(self, encoding: str | None) -> None:
        self.encoding = encoding
        self._chunks: list[str] = []

    def write(self, data: str) -> int:
        self._chunks.append(data)
        return len(data)

    def flush(self) -> None:
        # ``print`` may call ``flush``; retain compatibility without side effects.
        return None

    def getvalue(self) -> str:
        return "".join(self._chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("utf-8", SUCCESS_BANNER),
        ("cp1252", ASCII_SUCCESS_BANNER),
        ("not-an-encoding", ASCII_SUCCESS_BANNER),
    ],
)
async def test_cli_emits_encoding_aware_success_banner(
    tmp_path: Path,
    stub_render: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    encoding: str,
    expected: str,
) -> None:
    """Exercise ``resolve_success_banner`` through the CLI entry point."""
    file = tmp_path / "diagram.md"
    file.write_text("""```mermaid\nA-->B\n```""")

    stream = _RecordingStream(encoding)
    monkeypatch.setattr(sys, "stdout", stream)

    exit_code = await main([file])

    assert exit_code == 0
    output = stream.getvalue().splitlines()
    assert output[-1] == expected
