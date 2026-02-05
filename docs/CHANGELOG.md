# Changelog

## Unreleased

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
