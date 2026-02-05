# nixie

`nixie` validates Mermaid diagrams embedded in Markdown files.

## Features

- Recursively searches directories for Markdown files while honouring
  `.gitignore`
- Scans the current directory for Markdown files when run without arguments
- Parses `mermaid` code blocks and uses `@mermaid-js/mermaid-cli` to validate
- Processes diagrams sequentially within each file to guarantee stable,
  bracketed output
- Prints clear error messages for failing diagrams

## Requirements

- Python 3.11+
- Node.js with `npx` or Bun with `bun x --bun` and `@mermaid-js/mermaid-cli`

## Installation

### From PyPI

End users can install the latest release directly from PyPI:

```bash
pip install nixie
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
nixie [--verbose] [--no-sandbox] [--mermaid-version VERSION] [FILE ...]
```

Diagrams are processed sequentially within each file to keep output stable.
Files are checked concurrently, but their buffered output is written between the
`==>` and `<==` markers in the order provided.
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

`--verbose` sets the `nixie.cli` logger to `INFO`, logging the exact
`mermaid-cli` command for each diagram. By default, nixie launches Puppeteer
with `--disable-setuid-sandbox`, `--disable-gpu`, and
`--disable-dev-shm-usage` for reliable headless operation. Use `--no-sandbox`
to also pass `--no-sandbox` to Chromium.
`--mermaid-version` selects the `@mermaid-js/mermaid-cli` version when nixie
launches `npx` or `bun`. The default is `latest`, and the flag is ignored when
`mmdc` is found on disk.

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

See `LICENSE` for license details.
