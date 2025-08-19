"""Shared pytest fixtures for unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_home_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Set ``Path.home`` and the working directory to temporary paths.

    This isolates tests from the real filesystem and ensures consistent
    locations for files expected under the user's home directory.

    Returns the patched home directory path.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    return home
