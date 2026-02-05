"""BDD scenarios for deterministic ordered output under concurrent execution."""

from __future__ import annotations

import asyncio
import typing as typ

import pytest
from pytest_bdd import given, scenarios, then, when

from nixie.cli import main

if typ.TYPE_CHECKING:
    from pathlib import Path

scenarios("features/deterministic_order.feature")

PARSE_ERROR_MESSAGE = "Parse error on line 1:\ninvalid\n^\n"


class _CapturedOutput(typ.Protocol):
    """Protocol for captured stdout/stderr output objects."""

    out: str
    err: str


class ScenarioState(typ.TypedDict, total=False):
    """Mutable scenario state shared across BDD steps."""

    paths: list[Path]
    file_a: Path
    file_b: Path
    completion_order: list[str]
    exit_code: int
    captured: _CapturedOutput


@pytest.fixture
def scenario_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScenarioState:
    """Create fixture files and monkeypatch diagram rendering behavior."""
    file_a = tmp_path / "ordered-a.md"
    file_b = tmp_path / "ordered-b.md"
    file_a.write_text(
        "\n".join(
            [
                "```mermaid",
                "sequenceDiagram %% slow-valid",
                "A->B",
                "```",
                "",
                "```mermaid",
                "classDiagram %% fast-invalid",
                "A--|>B",
                "```",
            ]
        )
    )
    file_b.write_text(
        "\n".join(
            [
                "```mermaid",
                "flowchart %% fast-valid",
                "A-->B",
                "```",
            ]
        )
    )
    completion_order: list[str] = []

    async def fake_render(
        block: str,
        _tmpdir: Path,
        _cfg_path: Path | None,
        _path: Path,
        _idx: int,
        timeout: float,
        mermaid_version: str = "latest",
    ) -> None:
        _ = timeout
        _ = mermaid_version
        if "slow-valid" in block:
            await asyncio.sleep(0.2)
            completion_order.append("a1")
            return
        if "fast-invalid" in block:
            await asyncio.sleep(0.01)
            completion_order.append("a2")
            raise RuntimeError(PARSE_ERROR_MESSAGE)
        await asyncio.sleep(0.02)
        completion_order.append("b1")

    monkeypatch.setattr("nixie.cli._render_diagram", fake_render)
    return {
        "paths": [file_a, file_b],
        "file_a": file_a,
        "file_b": file_b,
        "completion_order": completion_order,
    }


@given("markdown fixtures with delayed and failing diagram checks")
def given_markdown_fixtures_with_delayed_checks(
    scenario_state: ScenarioState,
) -> None:
    """Load fixture state for the scenario."""
    _ = scenario_state


@when("I validate the fixtures with nixie")
def when_i_validate_the_fixtures_with_nixie(
    scenario_state: ScenarioState,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the CLI main loop for the prepared fixture files."""
    paths = scenario_state["paths"]
    scenario_state["exit_code"] = asyncio.run(main(paths, max_concurrency=3))
    scenario_state["captured"] = capsys.readouterr()


@then("diagram markers are emitted in deterministic file and diagram order")
def then_diagram_markers_are_emitted_in_deterministic_order(
    scenario_state: ScenarioState,
) -> None:
    """Assert deterministic output order despite out-of-order completions."""
    completion_order = scenario_state["completion_order"]
    assert completion_order == ["a2", "b1", "a1"]

    captured = scenario_state["captured"]
    lines = captured.out.splitlines()

    file_a = scenario_state["file_a"]
    file_b = scenario_state["file_b"]

    expected = [
        f"==> {file_a}",
        "--> line 2: sequenceDiagram",
        "<-- line 4: sequenceDiagram",
        "--> line 7: classDiagram",
        "<-- line 9: classDiagram",
        f"<== {file_a}",
        f"==> {file_b}",
        "--> line 2: flowchart",
        "<-- line 4: flowchart",
        f"<== {file_b}",
    ]
    positions = [lines.index(marker) for marker in expected]
    assert positions == sorted(positions)


@then("the failing diagram error is reported on stderr")
def then_the_failing_diagram_error_is_reported_on_stderr(
    scenario_state: ScenarioState,
) -> None:
    """Assert failing diagram diagnostics are surfaced through stderr."""
    assert scenario_state["exit_code"] == 1

    captured = scenario_state["captured"]
    assert "Parse error on line 1" in captured.err
