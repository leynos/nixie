import asyncio
import sys
from pathlib import Path

import pytest

from nixie.cli import main


@pytest.fixture
def stub_render(monkeypatch):
    async def fake_render_block(
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

    monkeypatch.setattr("nixie.cli.render_block", fake_render_block)
    return fake_render_block


def test_single_file_success(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "good.md"
    md.write_text("""```mermaid
A-->B
```""")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""


def test_single_file_failure(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "bad.md"
    md.write_text("""```mermaid
INVALID
```""")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Parse error" in captured.err


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


def test_no_diagrams(tmp_path: Path, stub_render, capsys) -> None:
    md = tmp_path / "none.md"
    md.write_text("No diagrams here")
    exit_code = asyncio.run(main([md], 2))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
