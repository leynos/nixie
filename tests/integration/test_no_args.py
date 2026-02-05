"""Integration tests for running ``nixie`` without arguments."""

from __future__ import annotations

import sys
import typing as typ

import pytest

from nixie import cli as cli_module

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


def test_cli_scans_cwd_when_no_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discover Markdown files in CWD when no paths are supplied."""
    keep = tmp_path / "keep.md"
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "skip.md").write_text("ignored")
    keep.write_text("ok")
    (tmp_path / ".gitignore").write_text("ignored/\n")

    captured: list[Path] = []

    async def fake_main(
        paths: cabc.Iterable[Path],
        *,
        no_sandbox: bool = False,
        mermaid_version: str = "latest",
        max_concurrency: int | None = None,
    ) -> int:
        _ = mermaid_version
        _ = max_concurrency
        captured.extend(paths)
        return 0

    monkeypatch.setattr(cli_module, "main", fake_main)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nixie"])

    with pytest.raises(SystemExit) as excinfo:
        cli_module.cli()

    exc = typ.cast("SystemExit", excinfo.value)
    assert exc.code == 0, "cli() must exit with code 0 when run without arguments"
    assert captured == [keep], (
        "cli() must pass exactly the discovered Markdown file(s) to main()"
    )


def test_cli_handles_empty_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit successfully when no Markdown files are present."""
    called = False

    async def fake_main(
        paths: cabc.Iterable[Path],
        *,
        no_sandbox: bool = False,
        mermaid_version: str = "latest",
        max_concurrency: int | None = None,
    ) -> int:
        nonlocal called
        _ = mermaid_version
        _ = max_concurrency
        called = True
        return 0

    monkeypatch.setattr(cli_module, "main", fake_main)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nixie"])

    with pytest.raises(SystemExit) as excinfo:
        cli_module.cli()

    exc = typ.cast("SystemExit", excinfo.value)
    assert exc.code == 0, (
        "cli() must exit with code 0 when no Markdown files are present"
    )
    assert not called, "cli() must not invoke main() when no Markdown files exist"
    captured = capsys.readouterr()
    assert captured.err.strip() == "No Markdown files found."
    assert captured.out == ""


def test_cli_accepts_no_sandbox_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass ``--no-sandbox`` through to :func:`main`."""
    (tmp_path / "file.md").write_text("ok")

    received_no_sandbox = False

    async def fake_main(
        paths: cabc.Iterable[Path],
        *,
        no_sandbox: bool = False,
        mermaid_version: str = "latest",
        max_concurrency: int | None = None,
    ) -> int:
        nonlocal received_no_sandbox
        _ = mermaid_version
        _ = max_concurrency
        received_no_sandbox = no_sandbox
        return 0

    monkeypatch.setattr(cli_module, "main", fake_main)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nixie", "--no-sandbox"])

    with pytest.raises(SystemExit) as excinfo:
        cli_module.cli()

    exc = typ.cast("SystemExit", excinfo.value)
    assert exc.code == 0, "cli() must exit with code 0 when --no-sandbox is set"
    assert received_no_sandbox is True, (
        "cli() must pass no_sandbox=True to main() when --no-sandbox is used"
    )
