import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nixie.cli import main


@pytest.fixture
def stub_render(monkeypatch) -> AsyncMock:
    async def side_effect(
        block: str,
        tmpdir: Path,
        cfg_path: Path,
        path: Path,
        idx: int,
        semaphore: asyncio.Semaphore,
        timeout: float = 30.0,
    ) -> bool:
        if "invalid" in block.lower():
            print("Parse error on line 1: INVALID", file=sys.stderr)
            return False
        return True

    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr("nixie.cli.render_block", mock)
    return mock


def test_single_file_success(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "good.md"
    md.write_text("""```mermaid
A-->B
```""")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert stub_render.await_count == 1
    args = stub_render.await_args_list[0].args
    assert args[3] == md
    assert args[4] == 1


def test_single_file_failure(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "bad.md"
    md.write_text("""```mermaid
INVALID
```""")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Parse error" in captured.err
    assert stub_render.await_count == 1
    args = stub_render.await_args_list[0].args
    assert args[3] == md
    assert args[4] == 1


def test_directory_and_multiple_files(tmp_path: Path, stub_render, capsys) -> None:
    dir_path = tmp_path / "docs"
    dir_path.mkdir()
    (dir_path / "one.md").write_text("""```mermaid
A-->B
```""")
    (dir_path / "two.md").write_text("""```mermaid
invalid diagram
```""")
    extra = tmp_path / "extra.md"
    extra.write_text("Just text")
    exit_code = asyncio.run(main([dir_path, extra], 2))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Parse error" in captured.err
    assert stub_render.await_count == 2
    paths = {call.args[3] for call in stub_render.await_args_list}
    assert {dir_path / "one.md", dir_path / "two.md"} == paths
    indices = sorted(call.args[4] for call in stub_render.await_args_list)
    assert indices == [1, 1]


def test_multiple_diagrams(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "multi.md"
    md.write_text(
        """```mermaid
A-->B
```

```mermaid
invalid
```"""
    )
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Parse error" in captured.err
    assert stub_render.await_count == 2
    indices = sorted(call.args[4] for call in stub_render.await_args_list)
    assert indices == [1, 2]
    assert all(call.args[3] == md for call in stub_render.await_args_list)


def test_no_diagrams(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "none.md"
    md.write_text("No diagrams here")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert stub_render.await_count == 0
