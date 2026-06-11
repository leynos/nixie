"""End-to-end tests for ``--renderer`` selection through the console script.

These drive :func:`nixie.cli.cli` with a patched ``sys.argv`` so the full
argparse → ``main`` → renderer-resolution → subprocess pipeline is exercised
against stubbed discovery and a stubbed subprocess layer.
"""

from __future__ import annotations

import sys
import typing as typ

import pytest

from nixie import cli as cli_module

if typ.TYPE_CHECKING:
    from pathlib import Path

MERMAN_PATH = "/usr/local/bin/merman-cli"
MMDC_PATH = "/usr/bin/mmdc"


@pytest.fixture
def spawned_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, ...]]:
    """Record subprocess command lines and isolate discovery from the host."""
    commands: list[tuple[str, ...]] = []

    async def fake_create_subprocess_exec(*cmd: str, **_kwargs: object) -> object:
        commands.append(cmd)
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr("nixie.cli.Path.home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    return commands


def _stub_discovery(monkeypatch: pytest.MonkeyPatch, available: dict[str, str]) -> None:
    """Make ``shutil.which`` discover exactly the ``available`` executables."""
    monkeypatch.setattr("nixie.cli.shutil.which", lambda cmd: available.get(cmd))


def _write_fixture(tmp_path: Path) -> Path:
    """Write a Markdown file containing a single valid Mermaid diagram."""
    fixture = tmp_path / "doc.md"
    fixture.write_text("```mermaid\nflowchart\nA-->B\n```\n")
    return fixture


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Invoke the console-script entry point and return its exit code."""
    monkeypatch.setattr(sys, "argv", ["nixie", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.cli()
    code = excinfo.value.code
    assert isinstance(code, int), (
        f"expected exit code to be int, got {type(code).__name__!r}: {code!r}"
    )
    return code


class TestRendererSelection:
    """End-to-end ``--renderer`` selection through the console script."""

    def test_cli_auto_prefers_merman(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spawned_commands: list[tuple[str, ...]],
    ) -> None:
        """Render with merman-cli by default when it is installed."""
        _stub_discovery(monkeypatch, {"merman-cli": MERMAN_PATH, "mmdc": MMDC_PATH})
        fixture = _write_fixture(tmp_path)

        exit_code = _run_cli(monkeypatch, [str(fixture)])

        assert exit_code == 0, f"expected exit code 0, got {exit_code!r}"
        assert len(spawned_commands) == 1, (
            "expected exactly 1 spawned command, got "
            f"{len(spawned_commands)}: {spawned_commands!r}"
        )
        assert spawned_commands[0][0] == MERMAN_PATH, (
            f"expected merman-cli at {MERMAN_PATH!r}, got {spawned_commands[0][0]!r}"
        )
        assert "--puppeteerConfigFile" not in spawned_commands[0], (
            "expected no --puppeteerConfigFile in merman command, got "
            f"{spawned_commands[0]!r}"
        )

    def test_cli_forced_mmdc_uses_legacy_pipeline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spawned_commands: list[tuple[str, ...]],
    ) -> None:
        """Render with mmdc and a Puppeteer config when mmdc is forced."""
        _stub_discovery(monkeypatch, {"merman-cli": MERMAN_PATH, "mmdc": MMDC_PATH})
        fixture = _write_fixture(tmp_path)

        exit_code = _run_cli(monkeypatch, ["--renderer", "mmdc", str(fixture)])

        assert exit_code == 0, f"expected exit code 0, got {exit_code!r}"
        assert len(spawned_commands) == 1, (
            "expected exactly 1 spawned command, got "
            f"{len(spawned_commands)}: {spawned_commands!r}"
        )
        assert spawned_commands[0][0] == MMDC_PATH, (
            f"expected mmdc at {MMDC_PATH!r}, got {spawned_commands[0][0]!r}"
        )
        assert "--puppeteerConfigFile" in spawned_commands[0], (
            "expected --puppeteerConfigFile in mmdc command, got "
            f"{spawned_commands[0]!r}"
        )

    def test_cli_forced_merman_fails_fast_when_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spawned_commands: list[tuple[str, ...]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exit 1 with an install hint, before any render, when merman is forced."""
        _stub_discovery(monkeypatch, {})
        fixture = _write_fixture(tmp_path)

        exit_code = _run_cli(monkeypatch, ["--renderer", "merman", str(fixture)])

        assert exit_code == 1, f"expected exit code 1, got {exit_code!r}"
        assert spawned_commands == [], (
            f"expected no subprocess to be spawned, got {spawned_commands!r}"
        )
        captured = capsys.readouterr()
        assert "merman-cli" in captured.err, (
            f"expected 'merman-cli' in stderr, got {captured.err!r}"
        )
        assert "cargo install merman-cli" in captured.err, (
            f"expected 'cargo install merman-cli' in stderr, got {captured.err!r}"
        )

    def test_cli_auto_without_any_renderer_keeps_node_guidance(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spawned_commands: list[tuple[str, ...]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Preserve today's per-diagram node-environment error in auto mode."""
        _stub_discovery(monkeypatch, {})
        fixture = _write_fixture(tmp_path)

        exit_code = _run_cli(monkeypatch, [str(fixture)])

        assert exit_code == 1, f"expected exit code 1, got {exit_code!r}"
        assert spawned_commands == [], (
            f"expected no subprocess to be spawned, got {spawned_commands!r}"
        )
        captured = capsys.readouterr()
        assert "No supported node environment found" in captured.err, (
            "expected 'No supported node environment found' in stderr, got "
            f"{captured.err!r}"
        )

    def test_cli_no_sandbox_with_merman_creates_no_puppeteer_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        spawned_commands: list[tuple[str, ...]],
    ) -> None:
        """Accept ``--no-sandbox`` as inert when the merman backend is resolved."""
        _stub_discovery(monkeypatch, {"merman-cli": MERMAN_PATH})
        fixture = _write_fixture(tmp_path)
        configs_created: list[object] = []
        real_create = cli_module.create_puppeteer_config

        def recording_create(*, force_no_sandbox: bool = False) -> object:
            ctx = real_create(force_no_sandbox=force_no_sandbox)
            configs_created.append(ctx)
            return ctx

        monkeypatch.setattr("nixie.cli.create_puppeteer_config", recording_create)

        exit_code = _run_cli(monkeypatch, ["--no-sandbox", str(fixture)])

        assert exit_code == 0, f"expected exit code 0, got {exit_code!r}"
        assert len(spawned_commands) == 1, (
            "expected exactly 1 spawned command, got "
            f"{len(spawned_commands)}: {spawned_commands!r}"
        )
        assert spawned_commands[0][0] == MERMAN_PATH, (
            f"expected merman-cli at {MERMAN_PATH!r}, got {spawned_commands[0][0]!r}"
        )
        assert configs_created == [], (
            f"expected no Puppeteer configs to be created, got {configs_created!r}"
        )
