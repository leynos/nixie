"""Common fixtures for integration tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.fixture
def stub_render(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``_render_diagram`` with a stub for predictable results."""

    async def side_effect(
        block: str,
        tmpdir: Path,
        cfg_path: Path | None,
        path: Path,
        idx: int,
        timeout: float,
        mermaid_version: str = "latest",
    ) -> None:
        _ = timeout
        _ = mermaid_version
        _ = tmpdir
        _ = cfg_path
        _ = path
        _ = idx
        if "invalid" in block.lower():
            raise RuntimeError("Parse error on line 1: INVALID")

    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr("nixie.cli._render_diagram", mock)
    return mock


def _build_packaging_project(
    destination_root: Path,
    *,
    include_makefile: bool = False,
) -> Path:
    """Copy the minimal project files needed for packaging tests."""
    project_root = Path(__file__).resolve().parents[2]
    build_root = destination_root / f"package-copy-{uuid4().hex}"
    build_root.mkdir()

    names = (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "nixie",
        *(("Makefile",) if include_makefile else ()),
    )

    for name in names:
        source = project_root / name
        destination = build_root / name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    return build_root


@pytest.fixture
def packaging_project_root(tmp_path: Path) -> Path:
    """Provide a copied project tree for packaging tests."""
    return _build_packaging_project(tmp_path)


@pytest.fixture
def packaging_project_root_with_makefile(tmp_path: Path) -> Path:
    """Provide a copied project tree including the Makefile."""
    return _build_packaging_project(tmp_path, include_makefile=True)
