import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


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
