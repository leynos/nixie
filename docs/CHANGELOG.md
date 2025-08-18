# Changelog

## Unreleased

- `--verbose` now sets the `nixie.cli` logger to INFO, logging the underlying
   `mermaid-cli` commands. The `render_block(verbose=...)` parameter is
   deprecated and has no effect; configure the `nixie.cli` logger directly
   instead.
- When run as root, nixie now invokes `mmdc` with a Puppeteer configuration
  that disables the sandbox (`--no-sandbox` and `--disable-setuid-sandbox`).
- `nixie` searches common install locations for `mmdc` before falling back to
  `bunx` or `npx`.
