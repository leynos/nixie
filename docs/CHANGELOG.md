# Changelog

## Unreleased

- Added support for [merman-cli](https://github.com/Latias94/merman), a
  headless Rust Mermaid renderer, removing the need for Node.js and Chromium
  when it is installed.
- Added `--renderer {auto,merman,mmdc}` to select the rendering backend. The
  default `auto` prefers `merman-cli` (searching `~/.cargo/bin` then `PATH`)
  and falls back to the existing `mmdc`/`bun`/`npx` discovery chain.
- Changed the default validation authority: with `merman-cli` installed,
  diagrams are validated by merman's Mermaid-11.15.0-compatible implementation
  rather than `@mermaid-js/mermaid-cli`. Use `--renderer mmdc` to restore the
  previous behaviour.
- A Puppeteer configuration is now only generated for the mmdc backend.
  `--no-sandbox` and `--mermaid-version` remain accepted but are inert under
  the merman backend.
- Deprecated implicit reliance on the Node-based rendering path; its removal
  is planned for a future release once parity has been assessed.
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
- Directory traversal honours `.gitignore` patterns in the working directory
  only. When run without arguments, nixie scans the current directory for
  Markdown files using those ignore rules (nested `.gitignore` files are
  ignored).
- `nixie` searches common install locations for `mmdc` before falling back to
  `bun` or `npx`.
- `--mermaid-version` controls the `@mermaid-js/mermaid-cli` version used when
  launching `npx` or `bun` (default: `latest`).
- `nixie` always passes `--disable-setuid-sandbox`, `--disable-gpu`, and
  `--disable-dev-shm-usage` to Puppeteer. A new `--no-sandbox` flag disables
  Chromium's sandbox when needed and is applied automatically when running as
  root.
