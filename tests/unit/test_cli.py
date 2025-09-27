"""Unit tests for helpers in :mod:`nixie.cli`."""

from types import SimpleNamespace

import pytest

from nixie.cli import ASCII_SUCCESS_BANNER, SUCCESS_BANNER, resolve_success_banner


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (SimpleNamespace(encoding="utf-8"), SUCCESS_BANNER),
        (SimpleNamespace(encoding="cp1252"), ASCII_SUCCESS_BANNER),
        (SimpleNamespace(encoding=None), SUCCESS_BANNER),
        (SimpleNamespace(encoding="not-an-encoding"), ASCII_SUCCESS_BANNER),
        (SimpleNamespace(encoding=1234), ASCII_SUCCESS_BANNER),
        (None, SUCCESS_BANNER),
    ],
)
def test_resolve_success_banner_unit(
    stream: SimpleNamespace | None, expected: str
) -> None:
    """Return an encoding-compatible banner for diverse stream metadata."""
    assert resolve_success_banner(stream) == expected
