"""BDD scenarios for renderer backend selection.

Each scenario stubs renderer discovery (``shutil.which`` plus a temporary
home directory so no real ``~/.cargo/bin`` leaks in) and records the spawned
command line, then drives :func:`nixie.cli.main` with an explicit
``--renderer`` choice.
"""

from __future__ import annotations

import asyncio
import typing as typ
from contextlib import contextmanager

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from nixie.cli import RendererChoice, create_puppeteer_config, main

if typ.TYPE_CHECKING:
    from pathlib import Path

scenarios("features/renderer_selection.feature")

MERMAN_PATH = "/usr/local/bin/merman-cli"
MMDC_PATH = "/usr/bin/mmdc"

_RENDERER_MODES: typ.Final[dict[str, RendererChoice]] = {
    "in auto renderer mode": "auto",
    "forcing the merman renderer": "merman",
    "forcing the mmdc renderer": "mmdc",
}


class _CapturedOutput(typ.Protocol):
    """Protocol for captured stdout/stderr output objects."""

    out: str
    err: str


class ScenarioState(typ.TypedDict, total=False):
    """Mutable scenario state shared across BDD steps."""

    paths: list[Path]
    spawned_commands: list[tuple[str, ...]]
    puppeteer_configs_created: list[Path]
    exit_code: int
    captured: _CapturedOutput


@pytest.fixture
def scenario_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScenarioState:
    """Isolate discovery and record spawned commands and Puppeteer configs."""
    state: ScenarioState = {
        "spawned_commands": [],
        "puppeteer_configs_created": [],
    }

    home = tmp_path / "home"
    monkeypatch.setattr("nixie.cli.Path.home", lambda: home)
    monkeypatch.chdir(tmp_path)

    async def fake_create_subprocess_exec(*cmd: str, **_kwargs: object) -> object:
        state["spawned_commands"].append(cmd)
        return object()

    async def fake_wait_for_proc(
        _proc: object, _path: Path, _idx: int, _timeout: float
    ) -> tuple[bool, bytes]:
        return True, b""

    @contextmanager
    def recording_puppeteer_config(
        *, force_no_sandbox: bool = False
    ) -> typ.Generator[Path]:
        with create_puppeteer_config(force_no_sandbox=force_no_sandbox) as cfg:
            state["puppeteer_configs_created"].append(cfg)
            yield cfg

    monkeypatch.setattr(
        "nixie.cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr("nixie.cli.wait_for_proc", fake_wait_for_proc)
    monkeypatch.setattr("nixie.cli.create_puppeteer_config", recording_puppeteer_config)
    return state


def _stub_discovery(monkeypatch: pytest.MonkeyPatch, available: dict[str, str]) -> None:
    """Make ``shutil.which`` discover exactly the ``available`` executables."""
    monkeypatch.setattr("nixie.cli.shutil.which", lambda cmd: available.get(cmd))


@given("merman-cli is installed and a Node environment is installed")
def given_merman_and_node_installed(
    scenario_state: ScenarioState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provide both merman-cli and mmdc on the simulated PATH."""
    _ = scenario_state
    _stub_discovery(monkeypatch, {"merman-cli": MERMAN_PATH, "mmdc": MMDC_PATH})


@given("merman-cli is not installed and a Node environment is installed")
def given_only_node_installed(
    scenario_state: ScenarioState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provide only mmdc on the simulated PATH."""
    _ = scenario_state
    _stub_discovery(monkeypatch, {"mmdc": MMDC_PATH})


@given("merman-cli is not installed")
def given_merman_absent(
    scenario_state: ScenarioState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provide no renderer executables on the simulated PATH."""
    _ = scenario_state
    _stub_discovery(monkeypatch, {})


@given("a Markdown fixture containing one valid diagram")
def given_markdown_fixture(scenario_state: ScenarioState, tmp_path: Path) -> None:
    """Write a Markdown file with a single valid Mermaid block."""
    fixture = tmp_path / "doc.md"
    fixture.write_text("```mermaid\nflowchart\nA-->B\n```\n")
    scenario_state["paths"] = [fixture]


@when(
    parsers.parse("I validate the fixture with nixie {mode}"),
)
def when_i_validate_with_renderer(
    scenario_state: ScenarioState,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Run the CLI main loop with the renderer choice implied by ``mode``."""
    renderer = _RENDERER_MODES[mode]
    scenario_state["exit_code"] = asyncio.run(
        main(scenario_state["paths"], renderer=renderer)
    )
    scenario_state["captured"] = capsys.readouterr()


@then("the diagram is rendered with merman-cli")
def then_rendered_with_merman(scenario_state: ScenarioState) -> None:
    """Assert the spawned command used merman-cli."""
    assert scenario_state["exit_code"] == 0, (
        f"expected exit code 0 but got {scenario_state['exit_code']}"
    )
    commands = scenario_state["spawned_commands"]
    assert len(commands) == 1, (
        f"expected exactly 1 spawned command but got {len(commands)}: {commands}"
    )
    assert commands[0][0] == MERMAN_PATH, (
        f"expected merman-cli at '{MERMAN_PATH}' but got '{commands[0][0]}'"
    )
    assert "--puppeteerConfigFile" not in commands[0], (
        "expected --puppeteerConfigFile absent from merman command but found it "
        f"in {commands[0]}"
    )


@then("no Puppeteer configuration file is created")
def then_no_puppeteer_config(scenario_state: ScenarioState) -> None:
    """Assert no Puppeteer configuration was generated."""
    assert scenario_state["puppeteer_configs_created"] == [], (
        "expected no Puppeteer config files created but got "
        f"{scenario_state['puppeteer_configs_created']}"
    )


@then("the diagram is rendered with the Node-based mermaid-cli")
def then_rendered_with_mmdc(scenario_state: ScenarioState) -> None:
    """Assert the spawned command used the Node-based mermaid-cli."""
    assert scenario_state["exit_code"] == 0, (
        f"expected exit code 0 but got {scenario_state['exit_code']}"
    )
    commands = scenario_state["spawned_commands"]
    assert len(commands) == 1, (
        f"expected exactly 1 spawned command but got {len(commands)}: {commands}"
    )
    assert commands[0][0] == MMDC_PATH, (
        f"expected mmdc at '{MMDC_PATH}' but got '{commands[0][0]}'"
    )
    assert "--puppeteerConfigFile" in commands[0], (
        "expected --puppeteerConfigFile present in mmdc command but absent from "
        f"{commands[0]}"
    )


@then("validation fails before any diagram is rendered")
def then_fails_before_rendering(scenario_state: ScenarioState) -> None:
    """Assert a non-zero exit with no subprocess spawned."""
    assert scenario_state["exit_code"] == 1, (
        f"expected exit code 1 (early failure) but got {scenario_state['exit_code']}"
    )
    assert scenario_state["spawned_commands"] == [], (
        f"expected no subprocess spawned but got {scenario_state['spawned_commands']}"
    )


@then("the error names merman-cli and how to install it")
def then_error_names_install_route(scenario_state: ScenarioState) -> None:
    """Assert stderr names the binary and an installation route."""
    captured = scenario_state["captured"]
    assert "merman-cli" in captured.err, (
        f"expected 'merman-cli' in stderr but got: {captured.err!r}"
    )
    assert "cargo install merman-cli" in captured.err, (
        "expected install-route hint 'cargo install merman-cli' in stderr but "
        f"got: {captured.err!r}"
    )
