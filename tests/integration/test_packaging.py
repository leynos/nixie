"""Integration tests for wheel packaging behaviour."""

from __future__ import annotations

import shutil
import subprocess
import typing as typ
import zipfile

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path


def _require_executable(name: str, *, purpose: str) -> str:
    """Return an executable path or skip when the host tool is unavailable."""
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} must be available to {purpose}")
    assert executable is not None
    return executable


def test_uv_build_excludes_unittests_from_the_wheel(
    packaging_project_root: Path,
) -> None:
    """Build a wheel copy and verify packaging metadata and contents."""
    build_root = packaging_project_root

    uv_executable = _require_executable("uv", purpose="build the wheel")

    result = subprocess.run(  # noqa: S603  # Static argv in test env; shell=False.
        [uv_executable, "build", "--wheel"],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    combined_output = result.stdout + result.stderr
    assert "Package 'nixie.unittests'" not in combined_output, (
        f"Expected build output to exclude `nixie.unittests`, got: {combined_output!r}"
    )

    wheel_path = next((build_root / "dist").glob("*.whl"), None)
    assert wheel_path is not None, "wheel file not found"
    assert wheel_path.name.startswith("nixie_cli-"), (
        f"Expected wheel_path.name to start with 'nixie_cli-', got: {wheel_path.name!r}"
    )

    with zipfile.ZipFile(wheel_path) as wheel_archive:
        packaged_files = wheel_archive.namelist()
        metadata_name = next(
            (
                packaged_file
                for packaged_file in packaged_files
                if packaged_file.endswith(".dist-info/METADATA")
            ),
            None,
        )
        assert metadata_name is not None, "METADATA file not found in wheel"
        entry_points_name = next(
            (
                packaged_file
                for packaged_file in packaged_files
                if packaged_file.endswith(".dist-info/entry_points.txt")
            ),
            None,
        )
        assert entry_points_name is not None, "entry_points.txt not found in wheel"
        metadata = wheel_archive.read(metadata_name).decode("utf-8")
        entry_points = wheel_archive.read(entry_points_name).decode("utf-8")

    assert "Name: nixie-cli" in metadata, (
        f"Expected 'Name: nixie-cli' in metadata, got: {metadata!r}"
    )
    assert "nixie = nixie.cli:cli" in entry_points, (
        f"Expected 'nixie = nixie.cli:cli' in entry_points, got: {entry_points!r}"
    )

    unittest_entries = [
        packaged_file
        for packaged_file in packaged_files
        if packaged_file.startswith("nixie/unittests/")
    ]
    assert unittest_entries == [], (
        f"Expected unittest_entries to be empty, got: {unittest_entries!r}"
    )


def test_make_clean_removes_the_build_directory(
    packaging_project_root_with_makefile: Path,
) -> None:
    """Ensure ``make clean`` clears stale build artefacts before packaging."""
    build_root = packaging_project_root_with_makefile
    stale_test_file = build_root / "build/lib/nixie/unittests/stale_test.py"
    stale_test_file.parent.mkdir(parents=True, exist_ok=True)
    stale_test_file.write_text("pass\n", encoding="utf-8")
    assert stale_test_file.exists(), (
        "Expected stale test file to exist before running `make clean`"
    )

    make_executable = _require_executable("make", purpose="clean the tree")

    result = subprocess.run(  # noqa: S603  # Static argv in test env; shell=False.
        [make_executable, "clean"],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    build_dir = build_root / "build"
    assert not build_dir.exists(), (
        f"Expected build_dir to be removed by `make clean`, got: {build_dir!s}"
    )
