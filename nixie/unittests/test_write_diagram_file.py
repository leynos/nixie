from __future__ import annotations

from pathlib import Path

from nixie.cli import _write_diagram_file


def test_write_diagram_file(tmp_path: Path) -> None:
    path = Path("doc.md")
    block = "A-->B"

    mmd, svg = _write_diagram_file(block, tmp_path, path, 1)

    assert mmd.read_text() == block
    assert svg == mmd.with_suffix(".svg")
    assert mmd.exists()
