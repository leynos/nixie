"""Unit tests for renderer discovery, resolution, and command dispatch."""

from __future__ import annotations

import shutil
import typing as typ

import pytest

from nixie.cli import (
    ALLOWED_EXECUTABLES,
    NoRendererAvailableError,
    ResolvedRenderer,
    find_merman_cli,
    get_merman_cmd,
    get_mmdc_cmd,
    get_renderer_cmd,
    resolve_renderer,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

MERMAN_ON_PATH: typ.Final[str] = "/usr/local/bin/merman-cli"


@pytest.fixture
def sample_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return sample mmd, svg, and config paths."""
    mmd = tmp_path / "diagram.mmd"
    svg = tmp_path / "diagram.svg"
    cfg = tmp_path / "cfg.json"
    return mmd, svg, cfg


def _which_merman_only(cmd: str) -> str | None:
    """Simulate a PATH where only merman-cli is installed."""
    return MERMAN_ON_PATH if cmd == "merman-cli" else None


def _which_nothing(_cmd: str) -> str | None:
    """Simulate a PATH with no renderer executables at all."""
    return None


def test_allowed_executables_includes_merman_cli() -> None:
    """Permit merman-cli through the executable allow-list."""
    assert "merman-cli" in ALLOWED_EXECUTABLES, (
        "expected merman-cli in ALLOWED_EXECUTABLES"
    )


class TestFindMermanCli:
    """Discovery of the merman-cli binary.

    The ``fake_home_cwd`` fixture isolates discovery from the host: it points
    ``Path.home()`` at an empty temporary directory so the developer's real
    ``~/.cargo/bin/merman-cli`` cannot leak in. Methods that build paths under
    the fake home take it as a parameter; the rest request it via
    :func:`pytest.mark.usefixtures` purely for that side effect.
    """

    def test_prefers_cargo_bin_over_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_home_cwd: Path,
    ) -> None:
        """Use ``~/.cargo/bin/merman-cli`` before consulting ``PATH``."""
        cargo_bin = fake_home_cwd / ".cargo" / "bin"
        cargo_bin.mkdir(parents=True)
        merman_path = cargo_bin / "merman-cli"
        merman_path.write_text("")
        merman_path.chmod(0o755)
        monkeypatch.setattr(shutil, "which", _which_merman_only)

        assert find_merman_cli() == str(merman_path), (
            "expected find_merman_cli to return cargo path"
        )

    @pytest.mark.usefixtures("fake_home_cwd")
    def test_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fall back to ``shutil.which`` when no cargo install exists."""
        monkeypatch.setattr(shutil, "which", _which_merman_only)

        assert find_merman_cli() == MERMAN_ON_PATH, (
            "expected find_merman_cli to fall back to PATH"
        )

    def test_skips_non_executable_cargo_candidate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_home_cwd: Path,
    ) -> None:
        """Ignore a cargo-installed file that lacks execute permission."""
        cargo_bin = fake_home_cwd / ".cargo" / "bin"
        cargo_bin.mkdir(parents=True)
        merman_path = cargo_bin / "merman-cli"
        merman_path.write_text("")
        merman_path.chmod(0o644)  # not executable
        monkeypatch.setattr(shutil, "which", _which_nothing)

        assert find_merman_cli() is None, (
            "expected non-executable cargo candidate to be ignored"
        )

    @pytest.mark.usefixtures("fake_home_cwd")
    def test_returns_none_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return ``None`` when merman-cli cannot be discovered."""
        monkeypatch.setattr(shutil, "which", _which_nothing)

        assert find_merman_cli() is None, "expected None when merman-cli absent"


@pytest.mark.usefixtures("fake_home_cwd")
class TestResolveRenderer:
    """Resolution of the requested renderer choice to a backend.

    All methods rely on ``fake_home_cwd`` to keep discovery hermetic; it is
    applied to the whole class via :func:`pytest.mark.usefixtures` since no
    method needs the fixture's return value.
    """

    def test_auto_prefers_merman(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prefer merman-cli in auto mode when it is installed."""
        monkeypatch.setattr(shutil, "which", _which_merman_only)

        resolved = resolve_renderer("auto")
        assert resolved.backend == "merman", "expected auto to prefer merman"
        assert resolved.needs_puppeteer_config is False, (
            "expected merman renderer not to need Puppeteer config"
        )

    def test_auto_falls_back_to_mmdc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fall back to the mmdc backend when merman-cli is absent."""
        monkeypatch.setattr(shutil, "which", _which_nothing)

        resolved = resolve_renderer("auto")
        assert resolved.backend == "mmdc", "expected auto to fall back to mmdc"
        assert resolved.needs_puppeteer_config is True, (
            "expected mmdc renderer to need Puppeteer config"
        )

    def test_forced_merman_raises_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise when merman is forced but merman-cli cannot be found."""
        monkeypatch.setattr(shutil, "which", _which_nothing)

        with pytest.raises(NoRendererAvailableError):
            resolve_renderer("merman")

    def test_forced_merman_error_names_install_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Name the binary and an install route in the failure message."""
        monkeypatch.setattr(shutil, "which", _which_nothing)

        with pytest.raises(NoRendererAvailableError) as err:
            resolve_renderer("merman")
        message = str(err.value)
        assert "merman-cli" in message, "expected error to name merman-cli"
        assert "cargo install merman-cli" in message, (
            "expected error to name cargo install route"
        )

    def test_forced_mmdc_even_when_merman_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Honour an explicit mmdc request even when merman-cli exists."""
        monkeypatch.setattr(shutil, "which", _which_merman_only)

        resolved = resolve_renderer("mmdc")
        assert resolved.backend == "mmdc", "expected forced mmdc backend"
        assert resolved.needs_puppeteer_config is True, (
            "expected forced mmdc to need Puppeteer config"
        )


@pytest.mark.usefixtures("fake_home_cwd")
class TestGetMermanCmd:
    """Command construction for the merman backend.

    ``fake_home_cwd`` isolates discovery for the whole class via
    :func:`pytest.mark.usefixtures`.
    """

    def test_command_shape(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_paths: tuple[Path, Path, Path],
    ) -> None:
        """Build exactly ``merman-cli -i <mmd> -o <svg>``."""
        mmd, svg, _cfg = sample_paths
        monkeypatch.setattr(shutil, "which", _which_merman_only)

        cmd = get_merman_cmd(mmd, svg)
        assert cmd == [MERMAN_ON_PATH, "-i", str(mmd), "-o", str(svg)], (
            "expected get_merman_cmd command shape"
        )

    def test_raises_when_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_paths: tuple[Path, Path, Path],
    ) -> None:
        """Raise when the binary vanishes between resolution and use."""
        mmd, svg, _cfg = sample_paths
        monkeypatch.setattr(shutil, "which", _which_nothing)

        with pytest.raises(NoRendererAvailableError):
            get_merman_cmd(mmd, svg)


@pytest.mark.usefixtures("fake_home_cwd")
class TestGetRendererCmd:
    """Dispatch between the merman and mmdc command builders.

    ``fake_home_cwd`` isolates discovery for the whole class via
    :func:`pytest.mark.usefixtures`.
    """

    def test_merman_backend_ignores_cfg_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_paths: tuple[Path, Path, Path],
    ) -> None:
        """Never pass a Puppeteer config to merman-cli."""
        mmd, svg, cfg = sample_paths
        monkeypatch.setattr(shutil, "which", _which_merman_only)
        renderer = ResolvedRenderer(backend="merman", needs_puppeteer_config=False)

        cmd = get_renderer_cmd(mmd, svg, cfg, renderer=renderer)
        assert "--puppeteerConfigFile" not in cmd, (
            "expected merman backend to omit Puppeteer config"
        )
        assert cmd == get_merman_cmd(mmd, svg), (
            "expected get_renderer_cmd to delegate to get_merman_cmd"
        )

    def test_merman_backend_omits_version_spec(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_paths: tuple[Path, Path, Path],
    ) -> None:
        """Ignore the mermaid-cli version for the merman backend."""
        mmd, svg, cfg = sample_paths
        monkeypatch.setattr(shutil, "which", _which_merman_only)
        renderer = ResolvedRenderer(backend="merman", needs_puppeteer_config=False)

        cmd = get_renderer_cmd(
            mmd, svg, cfg, renderer=renderer, mermaid_version="10.9.1"
        )
        assert all("mermaid-cli" not in part for part in cmd), (
            "expected merman backend to omit mermaid-cli version spec"
        )
        assert cmd == [MERMAN_ON_PATH, "-i", str(mmd), "-o", str(svg)], (
            "expected merman backend command shape"
        )

    def test_mmdc_backend_delegates_byte_for_byte(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_paths: tuple[Path, Path, Path],
    ) -> None:
        """Match ``get_mmdc_cmd`` output exactly for the mmdc backend."""
        mmd, svg, cfg = sample_paths
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd: "/usr/bin/bun" if cmd == "bun" else None,
        )
        renderer = ResolvedRenderer(backend="mmdc", needs_puppeteer_config=True)

        cmd = get_renderer_cmd(
            mmd, svg, cfg, renderer=renderer, mermaid_version="10.9.1"
        )
        assert cmd == get_mmdc_cmd(mmd, svg, cfg, mermaid_version="10.9.1"), (
            "expected mmdc backend to delegate byte-for-byte"
        )
