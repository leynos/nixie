# Nixie users' guide

Nixie validates Mermaid diagrams embedded in Markdown files. It extracts every
` ```mermaid ` fenced code block, renders each one to SVG with an external
Mermaid renderer, and reports any block that fails to render. A non-zero exit
code makes it suitable as a documentation gate in CI.

## Installation

Install the latest release as a `uv` tool:

```bash
uv tool install nixie-cli
```

This provides the `nixie` command. A Mermaid renderer must also be installed;
see the next section.

## Renderers

Nixie supports two rendering backends and selects one **once per invocation**:

- **merman** — [merman-cli](https://github.com/Latias94/merman), a headless
  Rust re-implementation of Mermaid. It renders without Node.js, npm packages,
  or Chromium. Install it with `cargo install merman-cli` or download a
  prebuilt binary from the
  [merman releases page](https://github.com/Latias94/merman/releases). Nixie
  looks for it in `~/.cargo/bin` first, then on `PATH`.
- **mmdc** — the official Node-based
  [`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli), which
  drives headless Chromium through Puppeteer. Nixie discovers it via
  `~/.bun/bin/mmdc`, `./node_modules/.bin/mmdc`, `~/.npm-global/bin/mmdc`, then
  `mmdc`, `bun`, or `npx` on `PATH`.

The `--renderer` flag selects between them:

- `auto`: Default. Prefer `merman-cli`; fall back to the mmdc chain.
- `merman`: Require `merman-cli`; exit 1 with a hint explaining how to install
  it if absent.
- `mmdc`: Always use the Node-based chain, even if merman is installed.

### Compatibility note

merman is an independent implementation targeting Mermaid 11.15.0. It tracks
upstream closely (its parity suite compares thousands of SVG baselines), but it
is not the official renderer: a diagram accepted by one backend may, at the
margins, be rejected by the other. If a diagram validates locally but fails in
CI (or vice versa), check which backend each environment resolved — `--verbose`
logs the exact command — and force `--renderer mmdc` where the official
renderer's verdict is required.

## Command line

```bash
nixie [--verbose] [--renderer {auto,merman,mmdc}] [--no-sandbox]
      [--mermaid-version VERSION] [--max-concurrency N] [FILE ...]
```

Positional `FILE` arguments may be Markdown files or directories. With no
arguments, nixie scans the current working directory for `.md` files, honouring
the top-level `.gitignore` (nested `.gitignore` files are ignored).

### Flags

- `--verbose` — set the `nixie.cli` logger to `INFO`, logging the exact
  renderer command for each diagram.
- `--renderer {auto,merman,mmdc}` — select the rendering backend (see
  above). Default: `auto`.
- `--no-sandbox` — **mmdc backend only.** Pass `--no-sandbox` to Chromium
  via the generated Puppeteer configuration (useful in Docker or when running
  as root; applied automatically when running as root). Accepted but inert when
  the merman backend is in use, since merman-cli launches no browser.
- `--mermaid-version VERSION` — **mmdc backend only.** The
  `@mermaid-js/mermaid-cli` version to use when launching via `npx` or `bun`
  (default: `latest`); ignored when an `mmdc` binary is found on disk. Inert
  under the merman backend.
- `--max-concurrency N` — bound the number of simultaneous renderer
  processes. Clamped to `max(1, cpu_count - 1)`.

### Examples

Validate with whichever backend is available, preferring merman-cli (the default
`auto` mode searches `~/.cargo/bin/merman-cli`, then `PATH`, then the `mmdc`/
`bun`/`npx` chain):

```bash
nixie docs/
nixie --renderer auto docs/
```

Require the Rust backend, failing fast if `merman-cli` is not installed:

```bash
nixie --renderer merman README.md
```

Force the official Node-based renderer, even when merman-cli is present:

```bash
nixie --renderer mmdc --no-sandbox README.md
```

### Exit codes

- `0` — all diagrams in the processed files validated successfully.
- `1` — at least one diagram failed to render, a processing error occurred,
  or `--renderer merman` was forced without `merman-cli` installed.

## Output format

Each file and diagram is bracketed deterministically, in input order:

```text
==> path/to/file.md
--> line 10: sequenceDiagram
<-- line 20: sequenceDiagram
<== path/to/file.md
```

Renderer diagnostics for a failing diagram are written to stderr between that
diagram's markers. The two backends emit differently shaped errors:

- **mmdc** reports parse failures as a `Parse error on line N:` block, which
  nixie condenses to the error line, the offending source, a caret pointer, and
  the expectation message.
- **merman-cli** prints its error text directly (for example
  `Mermaid error: …`); nixie passes it through verbatim.

When `--renderer merman` is forced and `merman-cli` cannot be found, nixie
prints one error before any diagram is processed:

```text
No Mermaid renderer available. Install merman-cli (cargo install
merman-cli) or a Node environment with @mermaid-js/mermaid-cli.
```

In `auto` mode on a machine with neither backend, each diagram instead reports
the historical node-environment guidance, preserving the behaviour of earlier
releases.
