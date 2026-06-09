#!/usr/bin/env python3
"""Command-line interface for validating Mermaid diagrams in Markdown files.

This module parses Markdown files, extracts Mermaid blocks, and validates them
by rendering each block with an external Mermaid renderer. Rendering is
scheduled concurrently with a bounded worker limit while output is emitted in
deterministic file/diagram order. The renderer is selected once per run:
``merman-cli`` (a headless Rust implementation) is preferred when installed,
with a fallback to the Node-based ``mermaid-cli`` via the ``mmdc``, ``npx``,
and ``bun`` executables.

Usage:
    nixie [--verbose] [--renderer {auto,merman,mmdc}] [--no-sandbox]
          [--mermaid-version VERSION] [FILE ...]

The ``--verbose`` flag sets the ``nixie.cli`` logger to ``INFO`` to emit the
underlying renderer commands.
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
from contextlib import contextmanager, nullcontext, suppress
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

    pathspec = SimpleNamespace(PathSpec=_ShimPathSpec)  # type: ignore[no-redef]  # ty: ignore[invalid-assignment]

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

ALLOWED_EXECUTABLES: typ.Final[frozenset[str]] = frozenset(
    {"mmdc", "bun", "npx", "merman-cli"}
)
WINDOWS_EXECUTABLE_SUFFIXES: typ.Final[tuple[str, ...]] = (".exe", ".cmd", ".bat")

MERMAN_EXECUTABLE: typ.Final[str] = "merman-cli"
RendererChoice = typ.Literal["auto", "merman", "mmdc"]
RENDERER_CHOICES: typ.Final[tuple[str, ...]] = ("auto", "merman", "mmdc")

DEFAULT_PUPPETEER_ARGS: typ.Final[tuple[str, ...]] = (
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
)

SUCCESS_BANNER: typ.Final[str] = "🧜‍♀️✨ All diagrams validated successfully!"
ASCII_SUCCESS_BANNER: typ.Final[str] = "All diagrams validated successfully!"
DEFAULT_MERMAID_VERSION: typ.Final[str] = "latest"
MERMAID_CLI_PACKAGE: typ.Final[str] = "@mermaid-js/mermaid-cli"


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


class NoRendererAvailableError(RuntimeError):
    """Indicates that no supported Mermaid renderer could be found."""

    def __init__(self) -> None:
        super().__init__(
            "No Mermaid renderer available. Install merman-cli "
            "(cargo install merman-cli) or a Node environment with "
            "@mermaid-js/mermaid-cli."
        )


@dc.dataclass(slots=True, frozen=True)
class ResolvedRenderer:
    """Renderer backend resolved once per CLI invocation.

    Attributes
    ----------
    backend
        The selected renderer implementation: ``merman`` runs ``merman-cli``
        directly, while ``mmdc`` uses the Node-based ``mermaid-cli`` discovery
        chain.
    needs_puppeteer_config
        ``True`` only for the ``mmdc`` backend, which launches Chromium via
        Puppeteer; ``merman-cli`` renders headlessly without a browser.
    """

    backend: typ.Literal["merman", "mmdc"]
    needs_puppeteer_config: bool


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


@dc.dataclass(slots=True, frozen=True)
class DiagramTask:
    """Task metadata for validating a single diagram."""

    ordinal: int
    file_index: int
    path: Path
    diagram_index: int
    diagram: Diagram
    tmpdir: Path


@dc.dataclass(slots=True, frozen=True)
class DiagramTaskResult:
    """Result payload produced by a completed diagram task."""

    task: DiagramTask
    success: bool
    stderr_message: str | None = None


@dc.dataclass(slots=True, frozen=True)
class FileValidationPlan:
    """Work plan and preparation status for one markdown file."""

    index: int
    path: Path
    diagram_ordinals: tuple[int, ...]
    preparation_error: str | None = None


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
def diagram_markers(diagram: Diagram) -> typ.Generator[None]:
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


def resolve_max_concurrency(
    requested_max_concurrency: int | None,
    *,
    cpu_count: int | None = None,
) -> int:
    """Return bounded worker concurrency.

    The automatic ceiling is ``max(1, cpu_count - 1)``. Explicit requests are
    clamped to that ceiling to ensure process creation remains bounded.
    """
    detected_cores = cpu_count if cpu_count is not None else os.cpu_count()
    limit = max(1, (detected_cores or 1) - 1)
    if requested_max_concurrency is None:
        return limit
    return max(1, min(requested_max_concurrency, limit))


@contextmanager
def create_puppeteer_config(
    *,
    force_no_sandbox: bool = False,
) -> typ.Generator[Path]:
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


def _resolve_mermaid_cli_package(version: str) -> str:
    """Return the mermaid-cli package spec for ``version``."""
    normalized = version.strip()
    if not normalized:
        normalized = DEFAULT_MERMAID_VERSION
    return f"{MERMAID_CLI_PACKAGE}@{normalized}"


def find_merman_cli() -> str | None:
    """Return the path to ``merman-cli`` or ``None`` when not installed.

    A cargo-installed binary in ``~/.cargo/bin`` is preferred so that users do
    not need to modify ``PATH`` just to run ``nixie``; otherwise ``PATH`` is
    searched via :func:`shutil.which`.
    """
    cargo_candidate = Path.home() / ".cargo" / "bin" / MERMAN_EXECUTABLE
    if cargo_candidate.is_file():
        if os.access(cargo_candidate, os.X_OK):
            return str(cargo_candidate)
        LOGGER.debug("Skipping non-executable %s", cargo_candidate)
    return shutil.which(MERMAN_EXECUTABLE)


def resolve_renderer(choice: RendererChoice) -> ResolvedRenderer:
    """Resolve ``choice`` to a concrete renderer backend.

    ``merman`` requires ``merman-cli`` and raises when it is absent. ``mmdc``
    always selects the Node-based backend; its own discovery chain raises
    later if no Node environment exists, preserving historical behaviour.
    ``auto`` prefers ``merman-cli`` and otherwise falls back to ``mmdc``.

    Raises
    ------
    NoRendererAvailableError
        If ``choice`` is ``merman`` and ``merman-cli`` cannot be found.
    """
    match choice:
        case "merman":
            if find_merman_cli() is None:
                raise NoRendererAvailableError
            return ResolvedRenderer(backend="merman", needs_puppeteer_config=False)
        case "mmdc":
            return ResolvedRenderer(backend="mmdc", needs_puppeteer_config=True)
        case _:
            if find_merman_cli() is not None:
                return ResolvedRenderer(backend="merman", needs_puppeteer_config=False)
            return ResolvedRenderer(backend="mmdc", needs_puppeteer_config=True)


def get_mmdc_cmd(
    mmd: Path,
    svg: Path,
    cfg_path: Path | None,
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
) -> list[str]:
    """Return the command to run mermaid-cli.

    The Mermaid CLI can be installed in several common locations. We first
    check these explicit paths so that users do not need to modify ``PATH``
    just to run ``nixie``. Only if ``mmdc`` is not found do we fall back to
    ``bun`` or ``npx``, using the requested mermaid-cli version. Absolute paths
    to ``mmdc`` are valid and are invoked directly.
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
            cmd = [cli, "--yes", _resolve_mermaid_cli_package(mermaid_version)]
        case "bun":
            cmd = [cli, "x", "--bun", _resolve_mermaid_cli_package(mermaid_version)]
        case _:
            cmd = [cli]
    if cfg_path is not None:
        cmd += ["--puppeteerConfigFile", str(cfg_path)]
    cmd += ["-i", str(mmd), "-o", str(svg)]
    return cmd


def get_merman_cmd(mmd: Path, svg: Path) -> list[str]:
    """Return the command to render via ``merman-cli``.

    merman-cli renders headlessly in Rust, so no Puppeteer configuration and
    no package-version spec apply.

    Raises
    ------
    NoRendererAvailableError
        If ``merman-cli`` cannot be found (e.g. removed between renderer
        resolution and use).
    """
    cli = find_merman_cli()
    if cli is None:
        raise NoRendererAvailableError
    return [cli, "-i", str(mmd), "-o", str(svg)]


def get_renderer_cmd(
    mmd: Path,
    svg: Path,
    cfg_path: Path | None,
    *,
    renderer: ResolvedRenderer,
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
) -> list[str]:
    """Return the render command for the resolved ``renderer`` backend.

    The ``cfg_path`` and ``mermaid_version`` parameters only apply to the
    ``mmdc`` backend; the ``merman`` backend ignores both.
    """
    if renderer.backend == "merman":
        return get_merman_cmd(mmd, svg)
    return get_mmdc_cmd(mmd, svg, cfg_path, mermaid_version=mermaid_version)


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
        timeout_message = f"{path}: diagram {idx} timed out"
        return (False, timeout_message.encode("utf-8"))
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
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
    *,
    renderer: ResolvedRenderer | None = None,
) -> None:
    """Write ``block`` to disk and invoke the selected renderer.

    This consolidates temporary file handling and CLI invocation so callers only
    coordinate error handling.

    Parameters
    ----------
    block
        Mermaid source code.
    tmpdir
        Directory for intermediate files.
    cfg_path
        Optional Puppeteer configuration passed to the mmdc backend.
    path
        Markdown file containing the diagram; used for naming only.
    idx
        Index of the diagram within ``path``.
    timeout
        Maximum time in seconds to wait for the CLI to finish.
    renderer
        Resolved renderer backend. ``None`` resolves ``auto`` per call,
        preserving the behaviour of legacy callers that predate renderer
        selection.

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

    resolved = renderer if renderer is not None else resolve_renderer("auto")
    cmd = get_renderer_cmd(
        mmd, svg, cfg_path, renderer=resolved, mermaid_version=mermaid_version
    )
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
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
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
        await _render_diagram(
            block,
            tmpdir,
            cfg_path,
            path,
            idx,
            timeout,
            mermaid_version=mermaid_version,
        )
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


def _stderr_message_from_exception(exc: Exception) -> str:
    """Return deterministic stderr text for a diagram execution failure."""
    if isinstance(exc, FileNotFoundError):
        cli = exc.filename or "mmdc"
        return (
            f"Error: '{cli}' not found. Install Node.js with npx or Bun to use "
            "@mermaid-js/mermaid-cli."
        )
    if isinstance(exc, NoNodeEnvironmentAvailableError):
        return (
            "No supported node environment found. Install mmdc directly, or install "
            "Node.js (npx) or Bun to use @mermaid-js/mermaid-cli."
        )
    return str(exc)


async def _run_diagram_task(
    task: DiagramTask,
    cfg_path: Path | None,
    semaphore: asyncio.Semaphore,
    *,
    mermaid_version: str,
    renderer: ResolvedRenderer,
) -> DiagramTaskResult:
    """Run a single diagram task under bounded concurrency."""
    async with semaphore:
        try:
            await _render_diagram(
                task.diagram.source,
                task.tmpdir,
                cfg_path,
                task.path,
                task.diagram_index,
                30.0,
                mermaid_version=mermaid_version,
                renderer=renderer,
            )
        except Exception as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            return DiagramTaskResult(
                task=task,
                success=False,
                stderr_message=_stderr_message_from_exception(exc),
            )
    return DiagramTaskResult(task=task, success=True)


def _emit_diagram_result(result: DiagramTaskResult) -> None:
    """Emit deterministic output for a completed diagram result."""
    diagram = result.task.diagram
    print(f"--> line {diagram.line_start}: {diagram.schema}", flush=True)
    if result.stderr_message:
        print(result.stderr_message, file=sys.stderr, flush=True)
    print(f"<-- line {diagram.line_end}: {diagram.schema}", flush=True)


def _prepare_validation_work(
    paths: list[Path],
    *,
    temp_roots: list[str],
) -> tuple[list[FileValidationPlan], list[DiagramTask]]:
    """Prepare file and diagram work plans with stable global ordinals."""
    file_plans: list[FileValidationPlan] = []
    diagram_tasks: list[DiagramTask] = []
    next_ordinal = 0

    for file_index, path in enumerate(paths):
        try:
            diagrams = parse_blocks(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001  # unexpected file-level failure
            file_plans.append(
                FileValidationPlan(
                    index=file_index,
                    path=path,
                    diagram_ordinals=(),
                    preparation_error=f"Validation task raised an exception: {exc}",
                )
            )
            continue

        diagram_ordinals: list[int] = []
        if diagrams:
            tmpdir = Path(temp_roots[file_index])
            for diagram_index, diagram in enumerate(diagrams, 1):
                ordinal = next_ordinal
                next_ordinal += 1
                diagram_ordinals.append(ordinal)
                diagram_tasks.append(
                    DiagramTask(
                        ordinal=ordinal,
                        file_index=file_index,
                        path=path,
                        diagram_index=diagram_index,
                        diagram=diagram,
                        tmpdir=tmpdir,
                    )
                )
        file_plans.append(
            FileValidationPlan(
                index=file_index,
                path=path,
                diagram_ordinals=tuple(diagram_ordinals),
            )
        )
    return file_plans, diagram_tasks


async def check_file(
    path: Path,
    cfg_path: Path | None,
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
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
                        mermaid_version=mermaid_version,
                    )
                except Exception:  # pragma: no cover - unexpected
                    LOGGER.exception("%s: unexpected error in diagram %s", path, idx)
                    success = False
            if not success:
                all_success = False
    return all_success


async def main(
    paths: cabc.Iterable[Path],
    *,
    no_sandbox: bool = False,
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
    max_concurrency: int | None = None,
    renderer: RendererChoice = "auto",
) -> int:
    """Run the CLI entry point.

    Diagram checks run concurrently across and within files using a global
    bounded worker pool. Output remains deterministic and bracketed in input
    file order and in-source diagram order.

    The renderer backend is resolved once per invocation. A Puppeteer
    configuration is only created for the mmdc backend; merman-cli renders
    headlessly without Chromium.
    """
    try:
        resolved_renderer = resolve_renderer(renderer)
    except NoRendererAvailableError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    cfg_ctx: typ.ContextManager[Path | None] = (
        create_puppeteer_config(force_no_sandbox=no_sandbox)
        if resolved_renderer.needs_puppeteer_config
        else nullcontext(None)
    )
    with (
        cfg_ctx as cfg_path,
        tempfile.TemporaryDirectory() as temp_root,
    ):
        all_paths = list(collect_markdown_files(paths))
        temp_roots = [str(Path(temp_root) / str(i)) for i in range(len(all_paths))]
        for root in temp_roots:
            Path(root).mkdir(parents=True, exist_ok=True)

        file_plans, diagram_tasks = _prepare_validation_work(
            all_paths,
            temp_roots=temp_roots,
        )

        semaphore = asyncio.Semaphore(resolve_max_concurrency(max_concurrency))
        running_tasks = [
            asyncio.create_task(
                _run_diagram_task(
                    task,
                    cfg_path,
                    semaphore,
                    mermaid_version=mermaid_version,
                    renderer=resolved_renderer,
                )
            )
            for task in diagram_tasks
        ]
        completions: typ.Iterator[typ.Awaitable[DiagramTaskResult]] = iter(
            asyncio.as_completed(running_tasks)
        )
        pending_results: dict[int, DiagramTaskResult] = {}
        all_success = True

        for file_plan in file_plans:
            print(f"==> {file_plan.path}")
            try:
                if file_plan.preparation_error:
                    all_success = False
                    print(file_plan.preparation_error)
                for ordinal in file_plan.diagram_ordinals:
                    while ordinal not in pending_results:
                        next_task = next(completions)
                        completed = await next_task
                        pending_results[completed.task.ordinal] = completed
                    result = pending_results.pop(ordinal)
                    _emit_diagram_result(result)
                    if not result.success:
                        all_success = False
            except Exception as exc:  # noqa: BLE001  pragma: no cover - unexpected
                # Catch unexpected errors so the CLI can continue processing.
                print(f"Validation task raised an exception: {exc}")
                all_success = False
            print(f"<== {file_plan.path}")
        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)
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
        help="Log renderer commands for each diagram",
    )
    parser.add_argument(
        "--renderer",
        choices=RENDERER_CHOICES,
        default="auto",
        help=(
            "Renderer backend: 'merman' uses merman-cli, 'mmdc' uses "
            "@mermaid-js/mermaid-cli, and 'auto' prefers merman-cli with an "
            "mmdc fallback (default: auto)."
        ),
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help=(
            "Force Puppeteer to disable its sandbox (useful in Docker or when "
            "running as root; mmdc backend only)"
        ),
    )
    parser.add_argument(
        "--mermaid-version",
        default=DEFAULT_MERMAID_VERSION,
        help=(
            "Version of @mermaid-js/mermaid-cli to use when launching via npx or bun "
            "(default: latest; mmdc backend only)."
        ),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help=(
            "Maximum concurrent diagram checks. Values are clamped to "
            "max(1, cpu_count - 1)."
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
    sys.exit(
        asyncio.run(
            main(
                paths,
                no_sandbox=parsed.no_sandbox,
                mermaid_version=parsed.mermaid_version,
                max_concurrency=parsed.max_concurrency,
                renderer=parsed.renderer,
            )
        )
    )


if __name__ == "__main__":
    cli()
