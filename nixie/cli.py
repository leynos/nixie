#!/usr/bin/env python3
"""Command-line interface for validating Mermaid diagrams in Markdown files.

This module parses Markdown files, extracts Mermaid blocks, and validates
them with the `mermaid-cli` tool. It supports concurrent rendering via
`asyncio` and falls back between `mmdc`, `npx`, and `bun` executables.

Usage:
    nixie [--concurrency N] [--verbose] path1.md [path2.md ...]
"""

from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess as asyncio_subprocess
import json
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
import typing
import typing as typ
import warnings
from contextlib import contextmanager, suppress
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

BLOCK_RE = re.compile(
    r"^```\s*mermaid\s*\n(.*?)\n```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

ALLOWED_EXECUTABLES: typ.Final[frozenset[str]] = frozenset({"mmdc", "bun", "npx"})


class UnexpectedExecutableError(ValueError):
    """Raised when an executable outside the allowed set is requested."""

    def __init__(self, executable: str) -> None:
        super().__init__(f"Unexpected executable: {executable}")


class ConcurrencyValueError(argparse.ArgumentTypeError):
    """Raised when a concurrency value less than one is supplied."""

    def __init__(self, value: str) -> None:
        super().__init__(f"concurrency must be at least 1 (got {value})")


def parse_blocks(text: str) -> list[str]:
    """Return all mermaid code blocks found in the text."""
    return BLOCK_RE.findall(text)


def collect_markdown_files(paths: cabc.Iterable[Path]) -> cabc.Generator[Path]:
    """Expand directories into Markdown files recursively."""
    for p in paths:
        if p.is_dir():
            for md in p.rglob("*"):
                if md.is_file() and md.suffix.lower() == ".md":
                    yield md
        else:
            yield p


@contextmanager
def create_puppeteer_config() -> typ.Generator[Path]:
    """Yield a Puppeteer config path and remove it on exit."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"args": ["--no-sandbox"]}, fh)
        fh.flush()
        name = fh.name
    path = Path(name)
    try:
        yield path
    finally:
        with suppress(OSError):
            path.unlink()


def get_mmdc_cmd(mmd: Path, svg: Path, cfg_path: Path) -> list[str]:
    """Return the command to run mermaid-cli."""
    cli = "mmdc"
    if not shutil.which("mmdc"):
        cli = "bun" if shutil.which("bun") else "npx"

    match cli:
        case "npx":
            cmd = ["npx", "--yes", "@mermaid-js/mermaid-cli", "mmdc"]
        case "bun":
            cmd = ["bun", "x", "--bun", "@mermaid-js/mermaid-cli", "mmdc"]
        case _:
            cmd = [cli]
    cmd += ["-p", str(cfg_path), "-i", str(mmd), "-o", str(svg)]
    return cmd


def format_cli_error(stderr: str) -> str:
    """Extract a concise parse error message from mmdc output."""
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r"Parse error on line (\d+):", line)
        if m and i + 2 < len(lines):
            snippet = lines[i + 1]
            pointer = lines[i + 2]
            detail = lines[i + 3] if i + 3 < len(lines) else ""
            return f"Parse error on line {m.group(1)}:\n{snippet}\n{pointer}\n{detail}"
    return stderr.strip()


async def wait_for_proc(
    proc: asyncio_subprocess.Process, path: Path, idx: int, timeout: float = 30.0
) -> tuple[bool, bytes]:
    """Wait for a process to complete and return its success status and stderr."""
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        print(f"{path}: diagram {idx} timed out", file=sys.stderr)
        return (False, b"")
    success = proc.returncode == 0
    return success, stderr


async def _run_mermaid_cli(
    cmd: list[str],
    sem: asyncio.Semaphore,
    path: Path,
    idx: int,
    timeout: float,
) -> tuple[bool, bytes]:
    if not cmd or cmd[0] not in ALLOWED_EXECUTABLES:
        raise UnexpectedExecutableError(cmd[0] if cmd else "")

    async with sem:
        # nosemgrep: python.lang.security.audit.dangerous-asyncio-create-exec-audit
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio_subprocess.PIPE,
            stderr=asyncio_subprocess.PIPE,
        )

    return await wait_for_proc(proc, path, idx, timeout)


async def _render_diagram(
    block: str,
    tmpdir: Path,
    cfg_path: Path,
    path: Path,
    idx: int,
    sem: asyncio.Semaphore,
    timeout: float,
) -> None:
    """Write ``block`` to disk and invoke ``mermaid-cli``.

    This consolidates temporary file handling and CLI invocation so callers only
    coordinate concurrency and error handling.

    Parameters
    ----------
    block
        Mermaid source code.
    tmpdir
        Directory for intermediate files.
    cfg_path
        Puppeteer configuration passed to the CLI.
    path
        Markdown file containing the diagram; used for naming only.
    idx
        Index of the diagram within ``path``.
    sem
        Semaphore limiting concurrent CLI executions.
    timeout
        Maximum time in seconds to wait for the CLI to finish.

    Raises
    ------
    RuntimeError
        If the CLI exits with a non-zero status.
    FileNotFoundError
        If the CLI executable cannot be found.
    """
    mmd = tmpdir / f"{path.stem}_{idx}.mmd"
    svg = mmd.with_suffix(".svg")
    mmd.write_text(block)

    cmd = get_mmdc_cmd(mmd, svg, cfg_path)
    if not cmd or cmd[0] not in ALLOWED_EXECUTABLES:
        raise UnexpectedExecutableError(cmd[0] if cmd else "")
    logging.getLogger(__name__).info(shlex.join(cmd))

    success, stderr = await _run_mermaid_cli(cmd, sem, path, idx, timeout)
    if not success:
        error_message = (
            f"Error running command {shlex.join(cmd)} for file '{path}' "
            f"(diagram {idx}):\n"
            f"{format_cli_error(stderr.decode('utf-8', errors='replace'))}"
        )
        raise RuntimeError(error_message)


async def render_block(
    block: str,
    tmpdir: Path,
    cfg_path: Path,
    path: Path,
    idx: int,
    semaphore: asyncio.Semaphore,
    *,
    timeout: float = 30.0,
    verbose: bool | None = None,
) -> bool:
    """Render a single mermaid block using the CLI asynchronously.

    Args:
        block: Mermaid code block to render.
        tmpdir: Temporary directory for intermediate files.
        cfg_path: Path to the Puppeteer configuration file.
        path: Markdown file containing the block.
        idx: Index of the block within ``path``.
        semaphore: Limits concurrent CLI invocations.
        timeout: Maximum time in seconds to wait for the CLI to finish.
        verbose: Deprecated. Configure logging to control command emission.

    Returns
    -------
        ``True`` on success, ``False`` otherwise.

    Notes
    -----
        The command line used for rendering is logged at ``INFO`` level.
    """
    if verbose is not None:
        warnings.warn(
            "render_block(verbose=...) is deprecated; configure logging level instead",
            DeprecationWarning,
            stacklevel=2,
        )
    try:
        await _render_diagram(block, tmpdir, cfg_path, path, idx, semaphore, timeout)
    except FileNotFoundError as exc:
        cli = exc.filename or "mmdc"
        print(
            "Error: "
            f"'{cli}' not found. Install Node.js with npx or Bun to use "
            "@mermaid-js/mermaid-cli.",
            file=sys.stderr,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
    else:
        return True
    return False


def default_concurrency() -> int:
    """Return a sensible default for the concurrency limit."""
    return os.cpu_count() or 4


async def check_file(
    path: Path,
    cfg_path: Path,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Check a single file for Mermaid diagrams."""
    blocks = parse_blocks(path.read_text(encoding="utf-8"))
    if not blocks:
        return True

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tasks = [
            render_block(
                block,
                tmp_path,
                cfg_path,
                path,
                idx,
                semaphore,
            )
            for idx, block in enumerate(blocks, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return all(result is True for result in results)


async def main(paths: cabc.Iterable[Path], max_concurrent: int) -> int:
    """Run the CLI entry point."""
    semaphore = asyncio.Semaphore(max_concurrent)
    with create_puppeteer_config() as cfg_path:
        all_success = True
        for path in collect_markdown_files(paths):
            print(f"==> {path}")
            try:
                success = await check_file(path, cfg_path, semaphore)
            except Exception as exc:  # noqa: BLE001  pragma: no cover - unexpected
                # Catch unexpected errors so the CLI can continue processing.
                print(f"Validation task raised an exception: {exc}")
                success = False
            if not success:
                all_success = False
            print(f"<== {path}")
        return 0 if all_success else 1


def positive_int(value: str) -> int:
    """Type for argparse to ensure a positive integer (>=1)."""
    ivalue = int(value)
    if ivalue < 1:
        raise ConcurrencyValueError(value)
    return ivalue


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate Mermaid diagrams in Markdown files"
    )
    parser.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="Markdown files to validate",
    )
    parser.add_argument(
        "--concurrency",
        type=positive_int,
        default=default_concurrency(),
        help="Maximum number of concurrent mmdc processes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log the command line of each mermaid-cli invocation",
    )
    return parser.parse_args()


def cli() -> None:
    """Entry point for the ``nixie`` console script."""
    parsed = parse_args()
    logging.basicConfig(
        level=logging.INFO if parsed.verbose else logging.WARNING,
        stream=sys.stderr,
    )
    sys.exit(asyncio.run(main(parsed.paths, parsed.concurrency)))


if __name__ == "__main__":
    cli()

