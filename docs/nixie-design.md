# Nixie Design

## Purpose

Nixie validates Mermaid diagrams in Markdown files.

The design has two non-negotiable outcomes:

- deterministic human-readable output, even under concurrency
- bounded process fan-out so large doc sets do not overwhelm the host

## Previous Approach (Sequential Validation)

The previous implementation used an async event loop but executed validation
work serially:

- files were processed one-by-one in `main`
- diagrams in a file were processed one-by-one in `check_file`
- each diagram spawned `mermaid-cli` and awaited completion before moving on

### Strengths

- simple control flow
- naturally deterministic output
- easy failure isolation

### Weaknesses

- low throughput for large repositories
- idle wall-clock time when independent diagrams could run in parallel
- async subprocess orchestration with little parallel benefit

## Revised Approach (Bounded Concurrent Scheduler)

The new implementation separates execution order from output order.

### Core Model

1. Collect files in deterministic order.
2. Parse each file and assign each diagram a stable identifier:
   - `file_index`
   - `diagram_index`
   - global `ordinal`
3. Schedule one async task per diagram across all files.
4. Guard task execution with a global semaphore.
5. Consume completed tasks as they arrive.
6. Emit output only when the next expected `ordinal` is available.

This enables across-file and within-file concurrency while preserving output
order.

### Renderer Backends

Nixie renders diagrams through one of two backends, resolved **once per
invocation** before any diagram work is scheduled (see
[ADR 0001](adr/0001-prefer-merman-cli-over-mmdc.md)):

- `merman` — [merman-cli](https://github.com/Latias94/merman), a headless
  Rust Mermaid implementation invoked as `merman-cli -i in.mmd -o out.svg`.
  No Puppeteer configuration is generated for this backend.
- `mmdc` — the Node-based `@mermaid-js/mermaid-cli`, discovered through the
  historical `mmdc`/`bun`/`npx` chain and given a generated Puppeteer
  configuration.

The `--renderer` flag selects `auto` (default; prefer merman, fall back to
mmdc), `merman` (required), or `mmdc` (forced). The resolved backend is
threaded to every diagram task as a frozen `ResolvedRenderer` value, so
workers never re-run discovery.

### Concurrency Limit

Nixie computes a safe automatic ceiling:

- `max(1, cpu_count - 1)`

Users can set `--max-concurrency`, but the value is clamped to that ceiling.
The runtime guarantee is:

- active `mermaid-cli` processes `<= max(1, cpu_count - 1)`

### Deterministic Emission Contract

For each file and diagram, output remains bracketed and ordered:

- file start marker: `==> path/to/file.md`
- diagram start marker: `--> line X: schema`
- stderr diagnostics for that diagram, if any
- diagram end marker: `<-- line Y: schema`
- file end marker: `<== path/to/file.md`

Even if diagram completion order is out-of-order, emission order is always
input file order and in-file diagram order.

### Error Handling

Each diagram task returns a structured result with:

- success/failure
- deterministic stderr payload (if failure)

Known failures include:

- Mermaid parse/runtime failures
- timeout failures
- a forced merman renderer with no `merman-cli` installed (reported once,
  before any diagram task runs)
- missing node runtime (`mmdc`, `bun`, `npx`) on the mmdc backend
- file-level preparation errors

No worker prints output directly. The emitter is the single writer, so ordering
remains stable.

## Performance Strategy

Nixie now optimizes throughput by parallelizing independent diagram checks while
retaining deterministic UX.

The benchmark corpus is copied into this repository under:

- `tests/fixtures/benchmark_docs`

These fixtures are copied from sibling projects so benchmark and e2e tests do
not read files in place from other repositories.

Routine benchmarks use a bounded subset under:

- `tests/fixtures/benchmark_sample`

The subset keeps benchmark runtime tractable while still exercising realistic
multi-file Mermaid workloads.

Benchmark workflow uses `hyperfine`:

- serial baseline: `--max-concurrency 1`
- bounded concurrent mode: default runtime limit

Example:

    make benchmark

## Testing Strategy

The design is validated at three layers:

- unit tests
  - concurrency limit clamping
  - timeout behavior payload
- integration tests
  - deterministic marker ordering under out-of-order completion
  - concurrency cap enforcement with controlled render delays
- BDD tests (`pytest-bdd`)
  - end-to-end scenario for deterministic output + stderr behavior with mixed
    success/failure and delayed completions

## Trade-offs

- The scheduler and emitter logic is more complex than a serial loop.
- Memory overhead increases slightly due to pending completed results that
  arrived early.
- Deterministic output is preserved without sacrificing bounded parallelism.

This trade-off is intentional: nixie remains predictable in CI logs while being
materially faster on large doc sets.
