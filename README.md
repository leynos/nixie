# nixie

`nixie` is a simple command-line tool that validates Mermaid diagrams embedded in Markdown files.

The CLI scans each provided file (or directory) for `mermaid` code blocks and
invokes `mermaid-cli` to verify that the diagrams can be rendered without
errors.

## Usage

```bash
nixie path/to/file.md
```

Use `--concurrency` to control how many diagrams are processed in parallel. The tool relies on
Node.js and `@mermaid-js/mermaid-cli` being available on your system.

## Development

Development dependencies are managed via `pyproject.toml`. After installing
[uv](https://github.com/astral-sh/uv), run:

```bash
uv sync --include dev
```

This installs linters and test tools such as Ruff, Pyright, pytest and
pytest-asyncio.

Before running the test suite, install the project in editable mode so the
package can be imported:

```bash
pip install -e .
```
