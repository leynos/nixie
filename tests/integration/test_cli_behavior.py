from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nixie.cli import main


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
