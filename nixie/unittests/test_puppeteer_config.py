"""Tests for :func:`nixie.cli.create_puppeteer_config`."""

from __future__ import annotations

import json
import os
import typing as typ

import pytest

from nixie.cli import DEFAULT_PUPPETEER_ARGS, create_puppeteer_config

if typ.TYPE_CHECKING:
    from pathlib import Path


class Scenario(typ.NamedTuple):
    """Parameters for :func:`create_puppeteer_config` scenarios."""

    euid: int
    force_no_sandbox: bool
    expected: list[str]


@pytest.mark.parametrize(
    "scenario",
    [
        Scenario(
            euid=0,
            force_no_sandbox=False,
            expected=[*DEFAULT_PUPPETEER_ARGS, "--no-sandbox"],
        ),
        Scenario(
            euid=1,
            force_no_sandbox=False,
            expected=list(DEFAULT_PUPPETEER_ARGS),
        ),
        Scenario(
            euid=1,
            force_no_sandbox=True,
            expected=[*DEFAULT_PUPPETEER_ARGS, "--no-sandbox"],
        ),
    ],
)
def test_create_puppeteer_config_args(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    """Generate the expected flag sets under different scenarios."""
    monkeypatch.setattr(os, "geteuid", lambda: scenario.euid, raising=False)
    with create_puppeteer_config(force_no_sandbox=scenario.force_no_sandbox) as cfg:
        data = json.loads(cfg.read_text())
        assert set(data["args"]) == set(scenario.expected), (
            "Puppeteer args should match the expected set",
        )
        path = cfg
    assert not path.exists(), "Temp config file should be removed on exit"


def test_create_puppeteer_config_cleanup_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean up the config file when the context exits with an error."""
    monkeypatch.setattr(os, "geteuid", lambda: 1, raising=False)
    path: Path | None = None

    def boom() -> None:
        nonlocal path
        with create_puppeteer_config() as cfg:
            path = cfg
            raise RuntimeError

    with pytest.raises(RuntimeError):
        boom()

    assert path is not None, "Context manager should yield a path"
    assert not path.exists(), "Temp config file should be removed on error"
