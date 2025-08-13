"""Integration tests for the CLI's high-level behaviour."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nixie.cli import main


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
    exit_code = await main(paths, 2)
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


@pytest.mark.asyncio
async def test_cli_marks_file_boundaries(
    tmp_path: Path, stub_render: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Emit clear boundary markers for each processed file."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("```mermaid\nA-->B\n```")
    file_b.write_text("No diagrams here")

    exit_code = await main([file_a, file_b], 2)
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
async def test_cli_handles_file_processing_error(
    tmp_path: Path,
    stub_render: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Handle exceptions from ``check_file`` without halting processing."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("```mermaid\nA-->B\n```")
    file_b.write_text("```mermaid\nA-->B\n```")

    from nixie import cli as cli_module

    original_check_file = cli_module.check_file

    async def mock_check_file(
        path: Path,
        cfg_path: Path | None,
        semaphore: asyncio.Semaphore,
        *args: object,
        **kwargs: object,
    ) -> bool:
        if path == file_b:
            raise SimulatedProcessingError()  # noqa: RSE102 - explicit instance for clarity
        return await original_check_file(path, cfg_path, semaphore, *args, **kwargs)

    monkeypatch.setattr(cli_module, "check_file", mock_check_file)

    exit_code = await main([file_a, file_b], 2)
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
