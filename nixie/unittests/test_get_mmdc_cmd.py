import shutil
from pathlib import Path

import pytest

from nixie.cli import get_mmdc_cmd


@pytest.fixture()
def sample_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    mmd = tmp_path / "diagram.mmd"
    svg = tmp_path / "diagram.svg"
    cfg = tmp_path / "cfg.json"
    return mmd, svg, cfg


def test_get_mmdc_cmd_with_bun(
    monkeypatch, sample_paths: tuple[Path, Path, Path]
) -> None:
    mmd, svg, cfg = sample_paths

    def which(cmd: str) -> str | None:
        if cmd == "bun":
            return "/usr/bin/bun"
        return None

    monkeypatch.setattr(shutil, "which", which)

    cmd = get_mmdc_cmd(mmd, svg, cfg)
    assert cmd[:3] == ["bun", "x", "--bun"]


def test_get_mmdc_cmd_with_npx(
    monkeypatch, sample_paths: tuple[Path, Path, Path]
) -> None:
    mmd, svg, cfg = sample_paths

    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    cmd = get_mmdc_cmd(mmd, svg, cfg)
    assert cmd[:4] == ["npx", "--yes", "@mermaid-js/mermaid-cli", "mmdc"]
