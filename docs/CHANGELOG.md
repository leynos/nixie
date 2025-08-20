# Changelog

## Unreleased

- `--verbose` now sets the `nixie.cli` logger to INFO, logging the underlying
   `mermaid-cli` commands. The `render_block(verbose=...)` parameter is
   deprecated and has no effect; configure the `nixie.cli` logger directly
   instead.
- When run as root, nixie now invokes `mmdc` with a Puppeteer configuration
  that disables the sandbox (`--no-sandbox` and `--disable-setuid-sandbox`).
- Directory traversal honours `.gitignore` patterns in the working directory only.
  When run without arguments, nixie scans the current directory for Markdown files
  using those ignore rules (nested `.gitignore` files are ignored).
- `nixie` searches common install locations for `mmdc` before falling back to
  `bun` or `npx`.
- `nixie` always passes `--disable-setuid-sandbox`, `--disable-gpu`, and
   `--disable-dev-shm-usage` to Puppeteer and exposes a `--no-sandbox` flag to
   disable Chromium's sandbox when needed.
