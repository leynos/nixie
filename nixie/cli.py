#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import typing
from contextlib import contextmanager
from pathlib import Path

if typing.TYPE_CHECKING:
    import collections.abc as cabc

BLOCK_RE = re.compile(
    r"^```\s*mermaid\s*\n(.*?)\n```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


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
def create_puppeteer_config() -> typing.Generator[Path]:
    """Yield a Puppeteer config path and remove it on exit."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"args": ["--no-sandbox"]}, fh)
        fh.flush()
        name = fh.name
    path = Path(name)
    try:
        yield path
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def get_mmdc_cmd(mmd: Path, svg: Path, cfg_path: Path) -> list[str]:
    """Return the command to run mermaid-cli."""
    cli = "mmdc" if shutil.which("mmdc") else "npx"
    cmd = [cli]
    if cli == "npx":
        cmd += ["--yes", "@mermaid-js/mermaid-cli", "mmdc"]
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
    proc: asyncio.subprocess.Process, path: Path, idx: int, timeout: float = 30.0
) -> tuple[bool, bytes]:
    """Wait for a process to complete and return its success status and stderr."""
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        print(f"{path}: diagram {idx} timed out", file=sys.stderr)
        return (False, b"")
    success = proc.returncode == 0
    return success, stderr


async def render_block(
    block: str,
    tmpdir: Path,
    cfg_path: Path,
    path: Path,
    idx: int,
    semaphore: asyncio.Semaphore,
    timeout: float = 30.0,
) -> bool:
    """Render a single mermaid block using the CLI asynchronously."""
    mmd = tmpdir / f"{path.stem}_{idx}.mmd"
    svg = mmd.with_suffix(".svg")

    try:
        mmd.write_text(block)

        cmd = get_mmdc_cmd(mmd, svg, cfg_path)
        cli = cmd[0]

        async with semaphore:
            try:
                proc = await asyncio.create_subprocess_exec(  # nosemgrep: python.lang.security.audit.dangerous-asyncio-create-exec-audit
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                print(
                    f"Error: '{cli}' not found. Node.js with npx and @mermaid-js/mermaid-cli is required.",
                    file=sys.stderr,
                )
                return False

            success, stderr = await wait_for_proc(proc, path, idx, timeout)
            if not success:
                print(
                    format_cli_error(stderr.decode("utf-8", errors="replace")),
                    file=sys.stderr,
                )
                return False
            return True

    except Exception as exc:
        print(f"{path}: unexpected error in diagram {idx}", file=sys.stderr)
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return False


def default_concurrency() -> int:
    """Return a sensible default for the concurrency limit."""
    return os.cpu_count() or 4


async def check_file(path: Path, cfg_path: Path, semaphore: asyncio.Semaphore) -> bool:
    """Check a single file for Mermaid diagrams."""
    blocks = parse_blocks(path.read_text(encoding="utf-8"))
    if not blocks:
        return True

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tasks = [
            render_block(block, tmp_path, cfg_path, path, idx, semaphore)
            for idx, block in enumerate(blocks, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return all(result is True for result in results)


async def main(paths, max_concurrent):
    """Main entry point."""
    semaphore = asyncio.Semaphore(max_concurrent)
    with create_puppeteer_config() as cfg_path:
        collected_files = collect_markdown_files(paths)
        tasks = [check_file(p, cfg_path, semaphore) for p in collected_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_success = True
        for result in results:
            if isinstance(result, Exception):
                print(f"Validation task raised an exception: {result}")
                all_success = False
            elif not result:
                all_success = False
        return 0 if all_success else 1


def positive_int(value: str) -> int:
    """Type for argparse to ensure a positive integer (>=1)."""
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(
            f"concurrency must be at least 1 (got {value})"
        )
    return ivalue


def parse_args():
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
    return parser.parse_args()


if __name__ == "__main__":
    parsed = parse_args()
    sys.exit(asyncio.run(main(parsed.paths, parsed.concurrency)))
