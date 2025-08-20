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
    """Include sandbox args automatically for the root user."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    path: Path | None = None
    with create_puppeteer_config() as cfg:
        path = cfg
        data = json.loads(cfg.read_text())
        assert data["args"] == [
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
    assert path is not None
    assert not path.exists()


def test_create_puppeteer_config_non_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide default flags even when not running as root."""
    monkeypatch.setattr(os, "geteuid", lambda: 1)
    path: Path | None = None
    with create_puppeteer_config() as cfg:
        path = cfg
        data = json.loads(cfg.read_text())
        assert data["args"] == [
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
        ]
    assert path is not None
    assert not path.exists()


def test_create_puppeteer_config_force_no_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow users to explicitly disable the sandbox."""
    monkeypatch.setattr(os, "geteuid", lambda: 1)
    with create_puppeteer_config(force_no_sandbox=True) as cfg:
        data = json.loads(cfg.read_text())
        assert data["args"] == [
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
