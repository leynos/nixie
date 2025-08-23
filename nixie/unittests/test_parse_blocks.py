"""Tests for ``parse_blocks`` utility."""

import pytest

from nixie.cli import UNKNOWN_SCHEMA, parse_blocks


@pytest.mark.parametrize(
    "text",
    [
        "```mermaid\nA-->B\n```",
        "``` mermaid\nA-->B\n``` ",
        "```mermaid   \nA-->B\n```   ",
        "```   mermaid   \nA-->B\n```",
    ],
)
def test_parse_blocks_variations(text: str) -> None:
    """Handle minor formatting variations around Mermaid blocks."""
    diagrams = parse_blocks(text)
    assert [d.source for d in diagrams] == ["A-->B"]
    assert [d.schema for d in diagrams] == [UNKNOWN_SCHEMA]


def test_parse_blocks_multiple() -> None:
    """Extract multiple Mermaid blocks from content."""
    content = "```mermaid\nA-->B\n```\n\n```mermaid\nC-->D\n```"
    diagrams = parse_blocks(content)
    assert [d.source for d in diagrams] == ["A-->B", "C-->D"]
    assert [d.schema for d in diagrams] == [UNKNOWN_SCHEMA, UNKNOWN_SCHEMA]
    assert [d.line_start for d in diagrams] == [2, 6]
    assert [d.line_end for d in diagrams] == [3, 7]


def test_parse_blocks_none() -> None:
    """Return an empty list when no blocks are present."""
    assert parse_blocks("No diagrams here") == []


def test_parse_blocks_empty() -> None:
    """Return an empty list for empty input."""
    assert parse_blocks("") == []


def test_parse_blocks_empty_and_whitespace() -> None:
    """Handle diagrams with missing or whitespace-only schema lines."""
    content_empty = "```mermaid\n\n```"
    diag_empty = parse_blocks(content_empty)
    assert len(diag_empty) == 1
    assert diag_empty[0].schema == UNKNOWN_SCHEMA
    assert diag_empty[0].source == ""
    assert diag_empty[0].line_start == 2
    assert diag_empty[0].line_end == 2

    content_ws = "```mermaid\n   \n```"
    diag_ws = parse_blocks(content_ws)
    assert len(diag_ws) == 1
    assert diag_ws[0].schema == UNKNOWN_SCHEMA
    assert diag_ws[0].source == "   "
    assert diag_ws[0].line_start == 2
    assert diag_ws[0].line_end == 3

    content_comment = "```mermaid\n%% a comment\nsequenceDiagram\nA->B\n```"
    diag_comment = parse_blocks(content_comment)
    assert diag_comment[0].schema == "sequenceDiagram"
    assert diag_comment[0].line_start == 2
    assert diag_comment[0].line_end == 5
