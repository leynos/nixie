# ruff: noqa: D100

from __future__ import annotations

import pytest

import nixie.cli as cli


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        ("", ""),
        ("mmdc", "mmdc"),
        ("bun", "bun"),
        ("npx", "npx"),
        ("mmdc.exe", "mmdc"),
        ("mmdc.EXE", "mmdc"),
        ("mmdc.cmd", "mmdc"),
        ("mmdc.CMD", "mmdc"),
        ("mmdc.bat", "mmdc"),
        ("/usr/bin/mmdc", "mmdc"),
        ("/usr/bin/mmdc.exe", "mmdc"),
        (r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.exe", "mmdc"),
        (r"C:\\Users\\runneradmin\\.bun\\bin\\MMDC.EXE", "mmdc"),
        ("  C:/Program Files/mermaid-cli/mmdc.EXE  ", "mmdc"),
        (r"C:\\Windows\\System32\\python.EXE", "python"),
    ],
)
def test_normalize_executable_name_known_suffix(executable: str, expected: str) -> None:
    """Normalise both POSIX and Windows paths with common suffixes."""
    assert cli._normalize_executable_name(executable) == expected


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        ("python", "python"),
        ("/usr/bin/python3", "python3"),
        ("/usr/local/bin/python3.11", "python3.11"),
        ("mmdc.exe.bak", "mmdc.exe.bak"),
        (r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.exe.bak", "mmdc.exe.bak"),
    ],
)
def test_normalize_executable_name_unknown_suffix(
    executable: str, expected: str
) -> None:
    """Leave unexpected suffixes untouched for downstream checks."""
    assert cli._normalize_executable_name(executable) == expected


@pytest.mark.parametrize(
    "executable",
    [
        "mmdc",
        "mmdc.exe",
        "mmdc.CMD",
        "mmdc.BAT",
        r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.EXE",
        r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.CMD",
        r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.BAT",
        "bun",
        "npx",
        r"C:\\Program Files\\nodejs\\npx.cmd",
    ],
)
def test_is_allowed_executable_accepts_allowlisted(executable: str) -> None:
    """Allow canonical names and Windows shims for the CLI."""
    assert cli._is_allowed_executable(executable)


@pytest.mark.parametrize(
    "executable",
    [
        "",
        "python",
        "python.exe",
        r"C:\\Windows\\System32\\python.exe",
        "mmdc.exe.bak",
        r"C:\\Users\\runneradmin\\.bun\\bin\\mmdc.exe.bak",
    ],
)
def test_is_allowed_executable_rejects_unexpected(executable: str) -> None:
    """Reject executables outside the allow-list."""
    assert not cli._is_allowed_executable(executable)
