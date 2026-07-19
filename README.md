# nixie

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/nixie)

`nixie` validates Mermaid diagrams embedded in Markdown files.

## Features

- Recursively searches directories for Markdown files while honouring
  `.gitignore`
- Scans the current directory for Markdown files when run without arguments
- Parses `mermaid` code blocks and validates them by rendering each one with
  [merman-cli](https://github.com/Latias94/merman) (preferred) or
  `@mermaid-js/mermaid-cli`
- Processes diagrams sequentially within each file to guarantee stable,
  bracketed output
- Prints clear error messages for failing diagrams

## Requirements

- Python 3.14+
- A Mermaid renderer, either of:
  - `merman-cli` (recommended): a headless Rust implementation with no
    Node.js or Chromium dependency. Install with `cargo install merman-cli`
    or download a release binary from the
    [merman releases page](https://github.com/Latias94/merman/releases).
  - Node.js with `npx` or Bun with `bun x --bun` and
    `@mermaid-js/mermaid-cli`.

By default, nixie prefers `merman-cli` when it is installed and falls back to
the Node-based `mermaid-cli` otherwise. Note that merman is an independent
re-implementation targeting Mermaid 11.15.0; acceptance may differ at the
margins from the official renderer. Use `--renderer mmdc` to validate with
the official `@mermaid-js/mermaid-cli` instead.

## Installation

### From PyPI

End users should install the latest release as a `uv` tool:

```bash
uv tool install nixie-cli
```

This provides the `nixie` command without any development extras.

### Development Setup

To contribute to nixie, install the package in editable mode and set up
development tools using [uv](https://docs.astral.sh/uv/), Astral's fast Python
package manager:

```bash
pip install -e .
uv sync --include dev
```

## Usage

```bash
nixie [--verbose] [--renderer {auto,merman,mmdc}] [--no-sandbox]
      [--mermaid-version VERSION] [--max-concurrency N] [FILE ...]
```

Diagram checks are scheduled concurrently across and within files using a
global worker limit. Output remains deterministic: file boundaries and diagram
markers are emitted in the order files and diagrams appear in the input.
`--max-concurrency` is clamped to `max(1, cpu_count - 1)`.
Paths can be files or directories. If no files are provided, nixie searches the
current working directory for Markdown files, excluding paths matched by
`.gitignore` in that directory. Discovery includes files with the `.md`
extension (case-sensitive). Files are processed in the order provided on the
command line.

### Exit codes

- 0 — All diagrams in processed files validated successfully.
- 1 — At least one diagram failed to render or a processing error occurred.

Only the `.gitignore` file in the working directory is used; nested
`.gitignore` files are ignored.

`--renderer` selects the rendering backend. `merman` uses `merman-cli`,
`mmdc` uses the Node-based `@mermaid-js/mermaid-cli`, and the default `auto`
prefers `merman-cli` (searching `~/.cargo/bin` then `PATH`) with a fallback
to the mmdc discovery chain. Forcing `--renderer merman` without `merman-cli`
installed exits with an error explaining how to install it.
`--verbose` sets the `nixie.cli` logger to `INFO`, logging the exact renderer
command for each diagram.

The following flags apply only to the `mmdc` backend and are accepted but
inert when the merman backend is in use. When using mmdc, nixie launches
Puppeteer with `--disable-setuid-sandbox`, `--disable-gpu`, and
`--disable-dev-shm-usage` for reliable headless operation. Use `--no-sandbox`
to also pass `--no-sandbox` to Chromium.
`--mermaid-version` selects the `@mermaid-js/mermaid-cli` version when nixie
launches `npx` or `bun`. The default is `latest`, and the flag is ignored when
`mmdc` is found on disk. `merman-cli` renders headlessly in Rust and needs no
Puppeteer configuration.
`--max-concurrency` bounds the number of simultaneous renderer processes.

When multiple files are provided, nixie prints markers that show where the
output for each file starts and ends. Each Mermaid diagram is also bracketed
with its line numbers and schema name. The start marker’s line number is the
first content line inside the fenced block; the end marker’s line number is the
closing fence line.

Schema detection:

- The schema is the first token on the first non-blank, non-comment line inside
  the fenced block. Lines starting with `%%` are treated as comments.
- If no such token exists, the schema is reported as `UNKNOWN_SCHEMA` (rendered
  as `<unknown>`).
- Schema names are echoed verbatim and are case-sensitive.

Example:

```text
==> path/to/file.md
--> line 10: sequenceDiagram
<-- line 20: sequenceDiagram
<== path/to/file.md
```

Errors reported while rendering a diagram appear between the `-->` and `<--`
lines for that diagram. Markers are printed on stdout; messages from
`mermaid-cli` are emitted on stderr. Most terminals interleave these streams by
write order, so the error lines will typically appear between the markers.

Example:

```bash
$ nixie bad.md
Parse error on line 1:
invalid diagram
^
Unexpected token: syntax error
```

## Development

Run formatting, linting, type checking and tests before committing:

```bash
ruff format
ruff check
pyright
pytest
```

The integration tests mock the CLI so Node.js is not needed during testing.

## Project Structure

- `nixie/cli.py` – command-line interface and validation logic
- `nixie/unittests/` – unit tests for helper functions
- `tests/integration/` – behavioural tests covering the CLI

## License

Licenced under the ISC licence. See `LICENSE` for license details.
