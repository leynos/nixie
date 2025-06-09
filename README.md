# nixie

`nixie` validates Mermaid diagrams embedded in Markdown files.

## Features

- Recursively searches directories for Markdown files
- Parses `mermaid` code blocks and uses `@mermaid-js/mermaid-cli` to validate
- Runs checks concurrently for faster feedback
- Prints clear error messages for failing diagrams

## Requirements

- Python 3.11+
- Node.js with `npx` and `@mermaid-js/mermaid-cli`

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
nixie [--concurrency N] FILE [FILE...]
```

`--concurrency` controls how many diagrams are processed in parallel (defaults
to the number of CPU cores or `4` if this cannot be determined). Paths can be
files or directories.

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
