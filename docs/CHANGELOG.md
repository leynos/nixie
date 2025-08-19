# Changelog

## Unreleased

- `--verbose` now sets the `nixie.cli` logger to INFO, logging the underlying
   `mermaid-cli` commands. The `render_block(verbose=...)` parameter is
   deprecated and has no effect; configure the `nixie.cli` logger directly
   instead.
- When run as root, nixie now invokes `mmdc` with a Puppeteer configuration
  that disables the sandbox (`--no-sandbox` and `--disable-setuid-sandbox`).
- Running `nixie` without arguments now scans the current directory for Markdown
  files, skipping paths matched by `.gitignore`.
