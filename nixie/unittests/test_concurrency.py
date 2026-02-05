"""Unit tests for concurrency helpers in :mod:`nixie.cli`."""

import pytest

from nixie.cli import resolve_max_concurrency


@pytest.mark.parametrize(
    ("requested", "cpu_count", "expected"),
    [
        (None, 8, 7),
        (None, 1, 1),
        (99, 4, 3),
        (2, 8, 2),
        (0, 8, 1),
        (-5, 8, 1),
    ],
)
def test_resolve_max_concurrency_bounds_workers(
    requested: int | None, cpu_count: int | None, expected: int
) -> None:
    """Clamp requested worker count to the safe automatic ceiling."""
    assert resolve_max_concurrency(requested, cpu_count=cpu_count) == expected
