"""Tests for diagram output markers."""

from __future__ import annotations

from unittest.mock import call, patch

from nixie.cli import Diagram, diagram_markers


def test_diagram_markers_flush() -> None:
    """Ensure per-diagram markers flush immediately to stdout."""
    diagram = Diagram("graph TD;", 1, 2, "graph")
    with patch("builtins.print") as mock_print, diagram_markers(diagram):
        pass
    mock_print.assert_has_calls(
        [
            call(f"--> line {diagram.line_start}: {diagram.schema}", flush=True),
            call(f"<-- line {diagram.line_end}: {diagram.schema}", flush=True),
        ]
    )
