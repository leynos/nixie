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
    assert parse_blocks(text) == ["A-->B"]


def test_parse_blocks_multiple() -> None:
    content = "```mermaid\nA-->B\n```\n\n```mermaid\nC-->D\n```"
    assert parse_blocks(content) == ["A-->B", "C-->D"]


def test_parse_blocks_none() -> None:
    assert parse_blocks("No diagrams here") == []
