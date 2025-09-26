#!/usr/bin/env python3
"""Command-line interface for validating Mermaid diagrams in Markdown files.

This module parses Markdown files, extracts Mermaid blocks, and validates them
with the `mermaid-cli` tool. Rendering occurs sequentially to keep output
bracketed and stable. The CLI falls back between `mmdc`, `npx`, and `bun`
executables.

Usage:
    nixie [--verbose] [FILE ...]

The ``--verbose`` flag sets the ``nixie.cli`` logger to ``INFO`` to emit the
underlying ``mermaid-cli`` commands.
"""

from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess as asyncio_subprocess
import bisect
import dataclasses as dc
import json
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
import typing as typ
import warnings
from contextlib import contextmanager, suppress
from pathlib import Path, PureWindowsPath

try:
    import pathspec  # type: ignore[unused-ignore]
except ModuleNotFoundError:  # pragma: no cover - test-only fallback
    # Minimal shim for offline testing when pathspec isn't installed.
    from types import SimpleNamespace

    @dc.dataclass(slots=True)
    class _Rule:
        kind: str  # "dir" or "file"
        value: str

    class _ShimPathSpec:
        def __init__(self, rules: list[_Rule]) -> None:
            self._rules = rules

        @classmethod
        def from_lines(cls, style: str, lines: list[str]) -> _ShimPathSpec:
            if style != "gitwildmatch":
                raise NotImplementedError
            rules: list[_Rule] = []
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.endswith("/"):
                    rules.append(_Rule("dir", line[:-1]))
                else:
                    rules.append(_Rule("file", line))
            return cls(rules)

        def match_file(self, rel_path: str) -> bool:
            for rule in self._rules:
                if rule.kind == "dir":
                    prefix = f"{rule.value}/" if rule.value else ""
                    if rel_path.startswith(prefix):
                        return True
                else:
                    if "/" not in rel_path and rel_path == rule.value:
                        return True
            return False

    pathspec = SimpleNamespace(PathSpec=_ShimPathSpec)  # type: ignore[no-redef]

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    TextIO = typ.TextIO

    class _EncodingAwareStream(typ.Protocol):
        encoding: str | None
else:
    TextIO = object  # type: ignore[assignment]
    _EncodingAwareStream = object

LOGGER = logging.getLogger(__name__)

BLOCK_RE = re.compile(
    r"^```\s*mermaid\s*\n(.*?)\n```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

ALLOWED_EXECUTABLES: typ.Final[frozenset[str]] = frozenset({"mmdc", "bun", "npx"})
WINDOWS_EXECUTABLE_SUFFIXES: typ.Final[tuple[str, ...]] = (".exe", ".cmd", ".bat")

DEFAULT_PUPPETEER_ARGS: typ.Final[tuple[str, ...]] = (
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
)

SUCCESS_BANNER: typ.Final[str] = "🧜‍♀️✨ All diagrams validated successfully!"
ASCII_SUCCESS_BANNER: typ.Final[str] = "All diagrams validated successfully!"


def resolve_success_banner(stream: _EncodingAwareStream | TextIO | None) -> str:
    """Return a stream-compatible success banner.

    Parameters
    ----------
    stream : _EncodingAwareStream | TextIO | None
        Output stream exposing an ``encoding`` attribute (e.g., ``sys.stdout``).

    Returns
    -------
    str
        :data:`SUCCESS_BANNER` when the stream supports it, otherwise the
        ASCII-only fallback.

    Notes
    -----
    Windows runners frequently default to ``cp1252`` and similar encodings that
    cannot represent the emoji in :data:`SUCCESS_BANNER`. Probe the encoding and
    use :data:`ASCII_SUCCESS_BANNER` whenever encoding fails or the codec is
    unknown so callers never see ``UnicodeEncodeError``.
    """
    if stream is None:
        return SUCCESS_BANNER
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return SUCCESS_BANNER
    try:
        SUCCESS_BANNER.encode(encoding)
    except (LookupError, TypeError, UnicodeError):
        # ``encode`` can raise ``LookupError`` for unknown encodings and generic
        # ``UnicodeError`` subclasses (e.g., ``UnicodeEncodeError``) when the
        # target codec lacks emoji support. ``TypeError`` guards against streams
        # exposing non-string ``encoding`` attributes. Fallback to ASCII in all
        # cases so printing never propagates encoding failures to callers.
        return ASCII_SUCCESS_BANNER
    return SUCCESS_BANNER


class UnexpectedExecutableError(ValueError):
    """Raised when an executable outside the allowed set is requested."""

    def __init__(self, executable: str) -> None:
        super().__init__(f"Unexpected executable: {executable}")


class NoNodeEnvironmentAvailableError(RuntimeError):
    """Indicates that neither mmdc nor a node environment could be found."""

    def __init__(self) -> None:
        super().__init__("No node environment available.")


@dc.dataclass(slots=True, frozen=True)
class Diagram:
    """Mermaid diagram extracted from a Markdown file.

    Attributes
    ----------
    source
        Raw Mermaid source inside the fenced code block (without backticks).
    line_start
        1-based line number for the first line of the block content.
    line_end
        1-based line number for the closing fence line.
    schema
        The diagram schema/name (e.g., ``sequenceDiagram``, ``classDiagram``,
        ``graph``).
    """

    source: str
    line_start: int
    line_end: int
    schema: str


UNKNOWN_SCHEMA: typ.Final[str] = "<unknown>"


def _extract_schema(lines: list[str]) -> str:
    """Return the schema name from ``lines``.

    Mermaid diagrams may start with empty lines or comments beginning with
    ``%%``. Skip these until a meaningful line is found. If no schema can be
    determined, return ``UNKNOWN_SCHEMA``.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        token = stripped.split()[0]
        return token if token.isalpha() else UNKNOWN_SCHEMA
    return UNKNOWN_SCHEMA


def parse_blocks(text: str) -> list[Diagram]:
    """Return all mermaid code blocks found in ``text``."""
    diagrams: list[Diagram] = []
    # Precompute newline offsets to avoid quadratic ``text.count`` calls when
    # deriving line numbers for each block.
    newline_offsets = [m.start() for m in re.finditer("\n", text)]
    for match in BLOCK_RE.finditer(text):
        block = match.group(1)
        line_start = bisect.bisect_left(newline_offsets, match.start(1)) + 1
        lines = block.splitlines()
        # ``splitlines`` returns ``[]`` for an empty block; in that case the
        # closing fence is on the same line as ``line_start``.
        line_end = line_start + len(lines)
        schema = _extract_schema(lines)
        diagrams.append(Diagram(block, line_start, line_end, schema))
    return diagrams


@contextmanager
def diagram_markers(diagram: Diagram) -> typ.Generator[None, None, None]:
    """Print markers bracketing ``diagram`` processing."""
    print(f"--> line {diagram.line_start}: {diagram.schema}", flush=True)
    try:
        yield
    finally:
        print(f"<-- line {diagram.line_end}: {diagram.schema}", flush=True)


def _load_gitignore_spec(root: Path) -> pathspec.PathSpec | None:
    """Return a ``PathSpec`` built from ``root/.gitignore`` if it exists."""
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        return pathspec.PathSpec.from_lines(
            "gitwildmatch", gitignore.read_text(encoding="utf-8").splitlines()
        )
    return None


def discover_markdown_files() -> cabc.Generator[Path]:
    """Yield Markdown files under the current directory respecting ``.gitignore``."""
    root = Path.cwd()
    spec = _load_gitignore_spec(root)
    # Use rglob with explicit sorting for deterministic results
    paths = sorted(root.rglob("*.md"))
    for path in paths:
        # Skip VCS metadata directories
        if ".git" in path.parts:
            continue
        rel_path = path.relative_to(root).as_posix()
        if spec and spec.match_file(rel_path):
            continue
        yield path


def collect_markdown_files(paths: cabc.Iterable[Path]) -> cabc.Generator[Path]:
    """Yield Markdown files under ``paths`` while honouring ``.gitignore``."""
    root = Path.cwd()
    spec = _load_gitignore_spec(root)
    for p in paths:
        if p.is_dir():
            for path in sorted(p.rglob("*.md")):
                try:
                    rel_path = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    rel_path = None
                if spec and rel_path and spec.match_file(rel_path):
                    continue
                yield path
        else:
            try:
                rel_path = p.resolve().relative_to(root).as_posix()
            except ValueError:
                rel_path = None
            # Only process Markdown files when an explicit file is provided
            if p.suffix.lower() != ".md":
                continue
            if spec and rel_path and spec.match_file(rel_path):
                continue
            yield p


@contextmanager
def create_puppeteer_config(
    *,
    force_no_sandbox: bool = False,
) -> typ.Generator[Path, None, None]:
    """Yield a temporary Puppeteer config ``Path`` and remove it on exit.

    ``mmdc`` relies on Chromium, which operates more reliably with a handful of
    default flags. We always include :data:`DEFAULT_PUPPETEER_ARGS` and append
    ``--no-sandbox`` when running as ``root`` or when
    ``force_no_sandbox`` is ``True``.
    """
    args = list(DEFAULT_PUPPETEER_ARGS)
    geteuid = getattr(os, "geteuid", lambda: 1)
    if force_no_sandbox or geteuid() == 0:
        args.append("--no-sandbox")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"args": args}, fh)
        fh.flush()
        name = fh.name
    path = Path(name)
    try:
        yield path
    finally:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def get_mmdc_cmd(mmd: Path, svg: Path, cfg_path: Path | None) -> list[str]:
    """Return the command to run mermaid-cli.

    The Mermaid CLI can be installed in several common locations. We first
    check these explicit paths so that users do not need to modify ``PATH``
    just to run ``nixie``. Only if ``mmdc`` is not found do we fall back to
    ``bun`` or ``npx``. Absolute paths to ``mmdc`` are valid and are invoked
    directly.
    """
    home = Path.home()
    cwd = Path.cwd()
    candidates: list[Path | str | None] = [
        home / ".bun" / "bin" / "mmdc",
        cwd / "node_modules" / ".bin" / "mmdc",
        home / ".npm-global" / "bin" / "mmdc",
        shutil.which("mmdc"),
        shutil.which("bun"),
        shutil.which("npx"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.is_file():
                if os.access(candidate, os.X_OK):
                    cli = str(candidate)
                    break
                LOGGER.debug("Skipping non-executable %s", candidate)
        elif candidate:
            cli = candidate
            break
    else:
        raise NoNodeEnvironmentAvailableError

    name = Path(cli).name
    match name:
        case "npx":
            cmd = [cli, "--yes", "@mermaid-js/mermaid-cli"]
        case "bun":
            cmd = [cli, "x", "--bun", "@mermaid-js/mermaid-cli"]
        case _:
            cmd = [cli]
    if cfg_path is not None:
        cmd += ["--puppeteerConfigFile", str(cfg_path)]
    cmd += ["-i", str(mmd), "-o", str(svg)]
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
    except asyncio.TimeoutError:  # noqa: UP041  # TODO(leynos): remove once ruff issue 8565 is fixed https://github.com/astral-sh/ruff/issues/8565
        proc.kill()
        await proc.wait()
        print(f"{path}: diagram {idx} timed out", file=sys.stderr)
        return (False, b"")
    success = proc.returncode == 0
    return success, stderr


def _normalize_executable_name(executable: str) -> str:
    """Return a normalized name for ``executable`` suitable for allow-listing."""
    if not executable:
        return ""
    # ``PureWindowsPath`` gracefully handles both POSIX and Windows style
    # separators without requiring platform checks. Always interpret the input
    # as a Windows path so that raw ``C:\\...`` strings normalise identically on
    # Linux hosts.
    cleaned = executable.strip()
    if not cleaned:
        return ""
    path = PureWindowsPath(cleaned)
    name = path.name.lower()
    for suffix in WINDOWS_EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return path.stem.lower()
    return name


def _is_allowed_executable(executable: str) -> bool:
    """Return ``True`` when ``executable`` is permitted to run mermaid-cli."""
    if not executable:
        return False
    normalized = _normalize_executable_name(executable)
    return normalized in ALLOWED_EXECUTABLES


async def _run_mermaid_cli(
    cmd: list[str],
    path: Path,
    idx: int,
    timeout: float,
) -> tuple[bool, bytes]:
    executable = cmd[0] if cmd else ""
    if not _is_allowed_executable(executable):
        raise UnexpectedExecutableError(executable)

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
    cfg_path: Path | None,
    path: Path,
    idx: int,
    timeout: float,
) -> None:
    """Write ``block`` to disk and invoke ``mermaid-cli``.

    This consolidates temporary file handling and CLI invocation so callers only
    coordinate error handling.

    Parameters
    ----------
    block
        Mermaid source code.
    tmpdir
        Directory for intermediate files.
    cfg_path
        Optional Puppeteer configuration passed to the CLI.
    path
        Markdown file containing the diagram; used for naming only.
    idx
        Index of the diagram within ``path``.
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
    LOGGER.info(shlex.join(cmd))
    success, stderr = await _run_mermaid_cli(cmd, path, idx, timeout)
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
    cfg_path: Path | None,
    path: Path,
    idx: int,
    *,
    timeout: float = 30.0,
    verbose: bool | None = None,
) -> bool:
    """Render a single mermaid block using the CLI asynchronously.

    Parameters
    ----------
    block : str
        Mermaid code block to render.
    tmpdir : Path
        Temporary directory for intermediate files.
    cfg_path : Path | None
        Optional path to the Puppeteer configuration file (omit when None).
    path : Path
        Markdown file containing the block.
    idx : int
        Index of the block within ``path``.
    timeout : float, default 30.0
        Maximum time in seconds to wait for the CLI to finish.
    verbose : bool, optional
        Deprecated; configure the ``nixie.cli`` logger instead.

    Returns
    -------
    bool
        ``True`` on success, ``False`` otherwise.

    Notes
    -----
    The command line used for rendering is logged at ``INFO`` level.
    """
    if verbose is not None:
        warnings.warn(
            "The 'verbose' parameter is deprecated; configure the 'nixie.cli'"
            " logger level instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    try:
        await _render_diagram(block, tmpdir, cfg_path, path, idx, timeout)
    except FileNotFoundError as exc:
        cli = exc.filename or "mmdc"
        LOGGER.exception(
            (
                "Error: '%s' not found. Install Node.js with npx or Bun to use "
                "@mermaid-js/mermaid-cli."
            ),
            cli,
        )
    except NoNodeEnvironmentAvailableError:
        LOGGER.error(  # noqa: TRY400  # user-facing error; suppress stack trace
            "No supported node environment found. Install mmdc directly, or install "
            "Node.js (npx) or Bun to use @mermaid-js/mermaid-cli.",
            exc_info=False,
        )
    except RuntimeError:
        LOGGER.exception("Runtime error while rendering diagram")
    except Exception as exc:
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        LOGGER.exception(
            "%s: unexpected error in diagram %s",
            path,
            idx,
        )
    else:
        return True
    return False


async def check_file(
    path: Path,
    cfg_path: Path | None,
) -> bool:
    """Check a single file for Mermaid diagrams."""
    diagrams = parse_blocks(path.read_text(encoding="utf-8"))
    if not diagrams:
        return True

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        all_success = True
        for idx, diagram in enumerate(diagrams, 1):
            with diagram_markers(diagram):
                try:
                    success = await render_block(
                        diagram.source,
                        tmp_path,
                        cfg_path,
                        path,
                        idx,
                    )
                except Exception:  # pragma: no cover - unexpected
                    LOGGER.exception("%s: unexpected error in diagram %s", path, idx)
                    success = False
            if not success:
                all_success = False
    return all_success


async def main(paths: cabc.Iterable[Path], *, no_sandbox: bool = False) -> int:
    """Run the CLI entry point.

    Processes files sequentially to keep output stable and bracketed. The
    ``--no-sandbox`` flag is passed through to Puppeteer when requested or
    when running as root.
    """
    with create_puppeteer_config(force_no_sandbox=no_sandbox) as cfg_path:
        all_success = True
        for path in collect_markdown_files(paths):
            print(f"==> {path}")
            try:
                success = await check_file(path, cfg_path)
            except Exception as exc:  # noqa: BLE001  pragma: no cover - unexpected
                # Catch unexpected errors so the CLI can continue processing.
                print(f"Validation task raised an exception: {exc}")
                success = False
            if not success:
                all_success = False
            print(f"<== {path}")
        if all_success:
            print(resolve_success_banner(sys.stdout), flush=True)
        return 0 if all_success else 1


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate Mermaid diagrams in Markdown files"
    )
    parser.add_argument(
        "paths",
        type=Path,
        nargs="*",
        help=(
            "Markdown files or directories to validate. When omitted, scans the "
            "current directory for .md files (honouring the top-level .gitignore; "
            "nested .gitignore files are ignored)."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log mermaid-cli commands for each diagram",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help=(
            "Force Puppeteer to disable its sandbox (useful in Docker or when "
            "running as root)"
        ),
    )
    return parser.parse_args()


def cli() -> None:
    """Entry point for the ``nixie`` console script."""
    parsed = parse_args()
    logger = LOGGER
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.INFO if parsed.verbose else logging.WARNING)
    if parsed.paths:
        paths = list(parsed.paths)
    else:
        paths = list(discover_markdown_files())
        if not paths:
            print("No Markdown files found.", file=sys.stderr)
            sys.exit(0)
    sys.exit(asyncio.run(main(paths, no_sandbox=parsed.no_sandbox)))


if __name__ == "__main__":
    cli()
