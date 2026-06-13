# ADR 0001: Prefer merman-cli over mmdc

Status: Accepted (2026-06-09)

## Context

Nixie validates Mermaid diagrams by rendering each block with an external
tool and treating a non-zero exit as failure. Until this decision, the only
backend was `@mermaid-js/mermaid-cli` (`mmdc`), which requires a Node.js or
Bun runtime and launches headless Chromium via Puppeteer. That dependency
chain is heavy for a documentation linter: CI images need Node plus a
working Chromium sandbox configuration, and nixie itself must generate
Puppeteer configuration files and offer `--no-sandbox` plumbing.

[merman](https://github.com/Latias94/merman) (`merman-cli` on crates.io,
0.7.0 at the time of writing, MSRV Rust 1.95) is a headless Rust
re-implementation of Mermaid targeting `mermaid@11.15.0`. It renders to SVG
with no Node, npm, or browser, exposes an mmdc-compatible root command
(`merman-cli -i in.mmd -o out.svg`), and maintains a published parity
dashboard across all 23 diagram families.

The trade-off: merman is an independent compatibility implementation, not
the official renderer. Switching the validation authority can change which
diagrams pass at the margins.

## Decision

In the context of validating Mermaid blocks in Markdown, facing the cost
and fragility of the Node/Chromium dependency chain, nixie adopts
`merman-cli` as its **preferred** renderer behind a
`--renderer {auto,merman,mmdc}` switch — with `auto` (the default)
preferring `merman-cli` and falling back to the existing `mmdc`/`bun`/`npx`
discovery chain — and against a hard cut-over, to give CI users one release
in which both backends coexist and failure deltas can be observed, while
accepting that the default validation authority changes on machines where
`merman-cli` is installed.

Supporting choices:

- Validation stays render-based (`-i`/`-o` to SVG), not `merman-cli parse`:
  nixie's contract is "this block renders", which subsumes parsing.
- `--puppeteerConfigFile` is never passed to merman-cli, even though
  upstream accepts it as a compatibility shim; a Puppeteer configuration is
  only generated for the mmdc backend.
- `--no-sandbox` and `--mermaid-version` remain accepted but are inert
  under the merman backend, preserving existing CI invocations.
- The renderer is resolved once per invocation into a frozen value rather
  than per diagram.

## Alternatives considered

- **Hard cut-over to merman-cli** — simplest code, removes all Node
  machinery immediately, but strands users whose diagrams depend on
  official-renderer behaviour and leaves no migration path inside one
  release.
- **Parse-only validation (`merman-cli parse`)** — faster, but weakens the
  guarantee from "renders" to "parses" and would diverge from the mmdc
  backend's semantics.
- **Stay on mmdc** — no compatibility risk, but keeps the Node, Puppeteer,
  and Chromium dependency chain that motivated this change.

## Consequences

- On machines with `merman-cli`, validation is faster and browser-free, but
  the authority is merman's Mermaid-11.15.0 implementation; `--renderer
  mmdc` restores the official renderer's verdict.
- Machines without `merman-cli` are unaffected.
- The Node-shaped pieces (`--mermaid-version`, the `bun`/`npx` fallback,
  Puppeteer configuration generation, `--no-sandbox`) are deprecated in
  place. Their removal is a separate, future decision gated on a
  fixture-corpus comparison of the two backends and field experience with
  `auto`.

## References

- ExecPlan: [docs/execplans/adopt-merman-cli.md](../execplans/adopt-merman-cli.md)
- Design document: [docs/nixie-design.md](../nixie-design.md)
- merman parity dashboard:
  <https://github.com/Latias94/merman/blob/main/docs/alignment/STATUS.md>
