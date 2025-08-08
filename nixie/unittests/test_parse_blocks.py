"""Tests for ``parse_blocks`` utility."""

import pytest

from nixie.cli import parse_blocks


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
    assert parse_blocks(text) == ["A-->B"]


def test_parse_blocks_multiple() -> None:
    """Extract multiple Mermaid blocks from content."""
    content = "```mermaid\nA-->B\n```\n\n```mermaid\nC-->D\n```"
    assert parse_blocks(content) == ["A-->B", "C-->D"]


def test_parse_blocks_none() -> None:
    """Return an empty list when no blocks are present."""
    assert parse_blocks("No diagrams here") == []


def test_parse_blocks_empty() -> None:
    """Return an empty list for empty input."""
    assert parse_blocks("") == []
