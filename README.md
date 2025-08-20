# nixie

`nixie` validates Mermaid diagrams embedded in Markdown files.

## Features

- Recursively searches directories for Markdown files while honouring
  `.gitignore`
- Scans the current directory for Markdown files when run without arguments
- Parses `mermaid` code blocks and uses `@mermaid-js/mermaid-cli` to validate
- Runs checks concurrently for faster feedback
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
nixie [--concurrency N] [--verbose] [--no-sandbox] [FILE ...]
```

`--concurrency` controls how many diagrams are processed in parallel (defaults
to the number of CPU cores or `4` if this cannot be determined). Paths can be
files or directories. If no files are provided, nixie searches the current
working directory for Markdown files, excluding paths matched by `.gitignore` in
that directory. Discovery includes files with the `.md` extension
(case-sensitive).

Only the `.gitignore` file in the working directory is used; nested
`.gitignore` files are ignored.

`--verbose` sets the `nixie.cli` logger to `INFO`, logging the exact
`mermaid-cli` command for each diagram. By default, nixie launches Puppeteer
with `--disable-setuid-sandbox`, `--disable-gpu`, and
`--disable-dev-shm-usage` for reliable headless operation. Use `--no-sandbox`
to also pass `--no-sandbox` to Chromium.

When multiple files are provided, nixie prints markers that show where the
output for each file starts and ends:

```text
==> path/to/file.md
<== path/to/file.md
```

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
