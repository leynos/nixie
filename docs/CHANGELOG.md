# Changelog

## Unreleased

- The PyPI distribution is now published as `nixie-cli` while keeping the
  installed console command as `nixie`.
- Diagram validation now runs concurrently across and within files with a
  bounded global worker limit. Output remains deterministic and ordered by file
  and diagram position.
- Added `--max-concurrency` to bound concurrent diagram checks. The configured
  value is clamped to `max(1, cpu_count - 1)`.
- Minimum supported Python version is now 3.14.
- `--verbose` now sets the `nixie.cli` logger to INFO, logging the underlying
   `mermaid-cli` commands. The `render_block(verbose=...)` parameter is
   deprecated and has no effect; configure the `nixie.cli` logger directly
   instead.
- Directory traversal honours `.gitignore` patterns in the working directory only.
  When run without arguments, nixie scans the current directory for Markdown files
  using those ignore rules (nested `.gitignore` files are ignored).
- `nixie` searches common install locations for `mmdc` before falling back to
  `bun` or `npx`.
- `--mermaid-version` controls the `@mermaid-js/mermaid-cli` version used when
  launching `npx` or `bun` (default: `latest`).
- `nixie` always passes `--disable-setuid-sandbox`, `--disable-gpu`, and
  `--disable-dev-shm-usage` to Puppeteer. A new `--no-sandbox` flag disables
  Chromium's sandbox when needed and is applied automatically when running as
  root.
