"""Property tests over the executable allow-list and command builders.

These properties guard two invariants over a wide input range:

- the allow-list cannot be bypassed by decorating an executable name with
  directories, Windows suffixes, mixed case, or surrounding whitespace; and
- every command built by :func:`nixie.cli.get_renderer_cmd` starts with an
  allow-listed executable and ends with the ``-i <mmd> -o <svg>`` pair.

Strategies compose valid inputs directly rather than filtering, per the
Hypothesis filtering-trap guidance. ``unittest.mock.patch`` context managers
are used instead of pytest's function-scoped ``monkeypatch`` fixture because
Hypothesis re-runs the test body many times per fixture instance.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

import nixie.cli as cli_module
from nixie.cli import (
    ALLOWED_EXECUTABLES,
    WINDOWS_EXECUTABLE_SUFFIXES,
    ResolvedRenderer,
    _is_allowed_executable,
    _normalize_executable_name,
    get_renderer_cmd,
)

# Base names deliberately exclude "." so that exactly zero or one known
# suffix decorates each generated executable; double suffixes (mmdc.exe.exe)
# are normalised one layer at a time and are out of scope for these
# invariants.
_BASE_NAME_ALPHABET: typ.Final[str] = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)

base_names = st.text(alphabet=_BASE_NAME_ALPHABET, min_size=1, max_size=20)

disallowed_base_names = base_names.filter(
    lambda name: name.lower() not in ALLOWED_EXECUTABLES
)

directory_prefixes = st.sampled_from(
    [
        "",
        "/usr/bin/",
        "/home/user/.cargo/bin/",
        "./",
        r"C:\Tools\bin" + "\\",
        r"C:\Users\runneradmin\.bun\bin" + "\\",
    ]
)

known_suffixes = st.sampled_from(["", *WINDOWS_EXECUTABLE_SUFFIXES])

paddings = st.sampled_from(["", " ", "  ", "\t"])


@st.composite
def decorated(draw: st.DrawFn, base_strategy: st.SearchStrategy[str]) -> str:
    """Decorate a base name with directory, suffix, case, and whitespace."""
    base = draw(base_strategy)
    prefix = draw(directory_prefixes)
    suffix = draw(known_suffixes)
    upper = draw(st.booleans())
    name = f"{base}{suffix}"
    if upper:
        name = name.upper()
    return f"{draw(paddings)}{prefix}{name}{draw(paddings)}"


@given(executable=st.text(max_size=50))
def test_normalize_output_is_lowercase(executable: str) -> None:
    """Return lowercase output for arbitrary input."""
    normalized = _normalize_executable_name(executable)
    assert normalized == normalized.lower()


@given(executable=decorated(base_names))
def test_normalize_recovers_base_name(executable: str) -> None:
    """Strip decoration down to the lowercased base name."""
    stripped = executable.strip()
    base = stripped.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in WINDOWS_EXECUTABLE_SUFFIXES:
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    assert _normalize_executable_name(executable) == base.lower()


@given(executable=decorated(base_names))
def test_normalize_is_idempotent_for_single_suffix_names(executable: str) -> None:
    """Normalise decorated single-suffix names to a fixed point."""
    once = _normalize_executable_name(executable)
    assert _normalize_executable_name(once) == once


@given(executable=decorated(disallowed_base_names))
def test_allowlist_cannot_be_bypassed_by_decoration(executable: str) -> None:
    """Reject non-allow-listed names however they are decorated."""
    assert not _is_allowed_executable(executable)


@given(executable=decorated(st.sampled_from(sorted(ALLOWED_EXECUTABLES))))
def test_allowlist_accepts_decorated_allowed_names(executable: str) -> None:
    """Accept allow-listed names under any supported decoration."""
    assert _is_allowed_executable(executable)


_DISCOVERY_PATHS: typ.Final[dict[str, str]] = {
    "merman-cli": "/usr/local/bin/merman-cli",
    "mmdc": "/usr/bin/mmdc",
    "bun": "/usr/bin/bun",
    "npx": "/usr/bin/npx",
}

file_stems = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=12
)


@given(
    stem=file_stems,
    backend=st.sampled_from(["merman", "mmdc"]),
    node_tool=st.sampled_from(["mmdc", "bun", "npx"]),
    with_cfg=st.booleans(),
)
def test_renderer_cmd_invariants(
    stem: str,
    backend: typ.Literal["merman", "mmdc"],
    node_tool: str,
    with_cfg: bool,  # noqa: FBT001  # Hypothesis draws positional bools
) -> None:
    """Start with an allow-listed executable and end with ``-i``/``-o``."""
    # The merman backend discovers merman-cli; the mmdc backend discovers
    # whichever Node tool the scenario installs. Pairing the discovered tool
    # with the backend composes only valid scenarios instead of filtering
    # out impossible ones (mmdc backend on a merman-only machine).
    discovered = "merman-cli" if backend == "merman" else node_tool

    def fake_which(cmd: str) -> str | None:
        return _DISCOVERY_PATHS[cmd] if cmd == discovered else None

    # Purely symbolic paths: the command builders stringify them without
    # touching the filesystem.
    mmd = Path(f"/render-scratch/{stem}.mmd")
    svg = mmd.with_suffix(".svg")
    cfg_path = Path("/render-scratch/cfg.json") if with_cfg else None
    renderer = ResolvedRenderer(
        backend=backend,
        needs_puppeteer_config=backend == "mmdc",
    )

    with (
        mock.patch.object(cli_module.shutil, "which", fake_which),
        mock.patch.object(
            cli_module.Path, "home", return_value=Path("/nonexistent-nixie-home")
        ),
    ):
        cmd = get_renderer_cmd(mmd, svg, cfg_path, renderer=renderer)

    assert _is_allowed_executable(cmd[0])
    assert cmd[-4:] == ["-i", str(mmd), "-o", str(svg)]
    if renderer.backend == "merman":
        assert "--puppeteerConfigFile" not in cmd
