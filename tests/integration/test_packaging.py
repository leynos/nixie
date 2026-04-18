"""Integration tests for wheel packaging behaviour."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def _copy_packaging_fixture(
    destination_root: Path,
    *,
    include_makefile: bool = False,
) -> Path:
    """Copy the minimal project files needed for packaging tests."""
    project_root = Path(__file__).resolve().parents[2]
    build_root = destination_root / "package-copy"
    build_root.mkdir()

    names = ["pyproject.toml", "README.md", "LICENSE", "nixie"]
    if include_makefile:
        names.append("Makefile")

    for name in names:
        source = project_root / name
        destination = build_root / name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    return build_root


def _require_executable(name: str, *, purpose: str) -> str:
    """Return an executable path or skip when the host tool is unavailable."""
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} must be available to {purpose}")
    assert executable is not None
    return executable


def test_uv_build_excludes_unittests_from_the_wheel(tmp_path: Path) -> None:
    """Build a wheel copy and verify packaging metadata and contents."""
    build_root = _copy_packaging_fixture(tmp_path)

    uv_executable = _require_executable("uv", purpose="build the wheel")

    result = subprocess.run(  # noqa: S603
        [uv_executable, "build", "--wheel"],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    combined_output = result.stdout + result.stderr
    assert "Package 'nixie.unittests'" not in combined_output

    wheel_path = next((build_root / "dist").glob("*.whl"), None)
    assert wheel_path is not None, "wheel file not found"
    assert wheel_path.name.startswith("nixie_cli-")

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

    assert "Name: nixie-cli" in metadata
    assert "nixie = nixie.cli:cli" in entry_points

    unittest_entries = [
        packaged_file
        for packaged_file in packaged_files
        if packaged_file.startswith("nixie/unittests/")
    ]
    assert unittest_entries == []


def test_make_clean_removes_the_build_directory(tmp_path: Path) -> None:
    """Ensure ``make clean`` clears stale build artefacts before packaging."""
    build_root = _copy_packaging_fixture(tmp_path, include_makefile=True)
    stale_test_file = build_root / "build/lib/nixie/unittests/stale_test.py"
    stale_test_file.parent.mkdir(parents=True, exist_ok=True)
    stale_test_file.write_text("pass\n", encoding="utf-8")
    assert stale_test_file.exists(), (
        "Expected stale test file to exist before running `make clean`"
    )

    make_executable = _require_executable("make", purpose="clean the tree")

    result = subprocess.run(  # noqa: S603
        [make_executable, "clean"],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (build_root / "build").exists()
