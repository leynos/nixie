"""Unit tests for :mod:`nixie.cli.get_mmdc_cmd`."""

import shutil
from pathlib import Path

import pytest

from nixie.cli import get_mmdc_cmd


@pytest.fixture
def sample_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return sample mmd, svg, and config paths."""
    mmd = tmp_path / "diagram.mmd"
    svg = tmp_path / "diagram.svg"
    cfg = tmp_path / "cfg.json"
    return mmd, svg, cfg


def test_get_mmdc_cmd_with_bun(
    monkeypatch: pytest.MonkeyPatch,
    sample_paths: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """Use Bun executable when available."""
    mmd, svg, cfg = sample_paths
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: "/usr/bin/bun" if cmd == "bun" else None,
    )

    cmd = get_mmdc_cmd(mmd, svg, cfg)
    assert cmd[:3] == ["bun", "x", "--bun"]


def test_get_mmdc_cmd_with_npx(
    monkeypatch: pytest.MonkeyPatch,
    sample_paths: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """Fall back to npx when neither Bun nor mmdc is available."""
    mmd, svg, cfg = sample_paths
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: "/usr/bin/npx" if cmd == "npx" else None,
    )

    cmd = get_mmdc_cmd(mmd, svg, cfg)
    assert cmd[:3] == ["npx", "--yes", "@mermaid-js/mermaid-cli"]


@pytest.mark.parametrize(
    "location",
    ["bun_home", "node_modules", "npm_global"],
)
def test_get_mmdc_cmd_custom_paths(
    location: str,
    monkeypatch: pytest.MonkeyPatch,
    sample_paths: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """Use `mmdc` from common installation paths before bun or npx."""
    mmd, svg, cfg = sample_paths
    home = tmp_path / "home"
    project = tmp_path / "project"
    bun_bin = home / ".bun" / "bin"
    node_bin = project / "node_modules" / ".bin"
    npm_bin = home / ".npm-global" / "bin"
    for path in (bun_bin, node_bin, npm_bin):
        path.mkdir(parents=True, exist_ok=True)

    if location == "bun_home":
        mmdc_path = bun_bin / "mmdc"
    elif location == "node_modules":
        mmdc_path = node_bin / "mmdc"
    else:
        mmdc_path = npm_bin / "mmdc"
    mmdc_path.write_text("")
    mmdc_path.chmod(0o755)

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    cmd = get_mmdc_cmd(mmd, svg, cfg)
    assert cmd[0] == str(mmdc_path)
