"""Common fixtures for integration tests."""

from __future__ import annotations

import typing as typ
from unittest.mock import AsyncMock

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path


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
