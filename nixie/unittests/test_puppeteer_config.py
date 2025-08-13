"""Tests for :func:`nixie.cli.create_puppeteer_config`."""

from __future__ import annotations

import json
import os
import typing as typ

from nixie.cli import create_puppeteer_config

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_create_puppeteer_config_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Include sandbox-disabling args when running as root."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    path: Path | None = None
    with create_puppeteer_config() as cfg:
        assert cfg is not None
        path = cfg
        data = json.loads(cfg.read_text())
        assert data["args"] == ["--no-sandbox", "--disable-setuid-sandbox"]
    assert path is not None
    assert not path.exists()


def test_create_puppeteer_config_non_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omit the config file when not running as root."""
    monkeypatch.setattr(os, "geteuid", lambda: 1)
    with create_puppeteer_config() as cfg:
        assert cfg is None
