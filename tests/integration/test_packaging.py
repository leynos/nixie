"""Integration tests for wheel packaging behaviour."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path


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


def test_uv_build_excludes_unittests_from_the_wheel(tmp_path: Path) -> None:
    """Build a wheel copy and verify that ``nixie.unittests`` is not packaged."""
    build_root = _copy_packaging_fixture(tmp_path)

    uv_executable = shutil.which("uv")
    assert uv_executable is not None, "uv must be available to build the wheel"

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

    wheel_path = next((build_root / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel_archive:
        packaged_files = wheel_archive.namelist()

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

    make_executable = shutil.which("make")
    assert make_executable is not None, "make must be available to clean the tree"

    result = subprocess.run(  # noqa: S603
        [make_executable, "clean"],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (build_root / "build").exists()
