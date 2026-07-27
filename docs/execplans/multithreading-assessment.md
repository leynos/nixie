# Refactor Nixie to Deterministic Concurrent Diagram Validation

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

## Purpose / Big Picture

Nixie currently validates diagrams one-by-one, which keeps output stable but
leaves significant performance on the table for large documentation sets. This
change introduces bounded concurrency across and within files while preserving
deterministic output ordering. After this change, users will be able to process
many diagrams in parallel with a hard execution limit and still receive
predictable, bracketed output in the same order as before.

The work also adds a design document (`docs/nixie-design.md`), updates Python
support to 3.14+, adds pytest-bdd coverage for deterministic ordered streaming,
and includes benchmark coverage using `hyperfine` with copied markdown fixture
documents.

## Constraints

- Preserve current output contract: `==> file`, `--> diagram start`, diagram
  diagnostics, `<-- diagram end`, `<== file` in deterministic order.
- Keep default maximum process concurrency at or below `max(1, cpu_count - 1)`.
- Do not read benchmark/e2e markdown fixtures from sibling repositories in
  place; copy them into this repository and test only against copied files.
- Maintain existing CLI behaviour and exit codes unless a behaviour change is
  explicitly documented in `docs/nixie-design.md` and tests.
- Pass repository quality gates relevant to touched files:
  - Python: `make check-fmt`, `make lint`, `make typecheck`, `make test`
  - Markdown: `make markdownlint`, `make nixie`

## Tolerances (Exception Triggers)

- Scope: if deterministic output requires a user-visible output format change
  beyond preserving marker ordering and existing textual structure, stop and
  escalate.
- Dependencies: if `pytest-bdd` or benchmark support requires more than two new
  dependencies, stop and escalate.
- Runtime: if quality gates exceed the command timeout constraints repeatedly,
  split execution into smaller chunks and continue; escalate only if unable to
  obtain reliable pass/fail signal.
- Iterations: if concurrency refactor still fails deterministic ordering tests
  after 3 correction cycles, pause and reassess approach before further edits.

## Risks

- Concurrent workers may emit logs/prints out of order unless all output is
  captured as structured events and emitted centrally.
- Error paths (timeouts, parse/runtime failures) currently print to stderr at
  worker time and can break deterministic order.
- Large copied fixture docs could slow test and benchmark suites if not scoped.
- `hyperfine` might be unavailable in CI/dev environments; benchmark workflow
  needs graceful fallback guidance.

## Progress

- [x] 2026-02-05 23:00 UTC: Inspected current concurrency model and confirmed
      sequential file and diagram processing in `nixie/cli.py`.
- [x] 2026-02-05 23:01 UTC: Confirmed documentation drift: README claims
      concurrent file processing while implementation is sequential.
- [x] 2026-02-05 23:11 UTC: Implemented deterministic concurrent scheduler with
      bounded global concurrency and ordered emission in `nixie/cli.py`.
- [x] 2026-02-05 23:13 UTC: Added unit and integration coverage for ordered
      streaming under out-of-order task completion and concurrency capping.
- [x] 2026-02-05 23:15 UTC: Added pytest-bdd scenario coverage for deterministic
      ordering and failure reporting.
- [x] 2026-02-05 23:16 UTC: Copied provided markdown design documents into
      `tests/fixtures/benchmark_docs`.
- [x] 2026-02-05 23:17 UTC: Added `make benchmark` using `hyperfine` with local
      copied fixtures.
- [x] 2026-02-05 23:17 UTC: Updated Python requirement to 3.14 and added
      `pytest-bdd` dependency.
- [x] 2026-02-05 23:18 UTC: Wrote `docs/nixie-design.md` and updated README and
      changelog documentation to match runtime behaviour.
- [x] 2026-02-05 23:22 UTC: Ran quality gates:
      `check-fmt`, `lint`, `typecheck`, `test`, `markdownlint` (via
      `npx markdownlint-cli`), and `nixie`.
- [x] 2026-02-05 23:23 UTC: Ran `hyperfine` benchmark comparison using a bounded
      smoke corpus (`docs/`) to keep runtime tractable in this environment.
- [x] 2026-02-05 23:24 UTC: Ran `make benchmark` on
      `tests/fixtures/benchmark_sample`; bounded-concurrency mode measured
      4.41x faster than `--max-concurrency 1` in this environment.
- [x] 2026-02-05 23:28 UTC: Committed implementation and test/doc updates in
      `b178161`.

## Surprises & Discoveries

- `grepai` status command is TUI-oriented in this environment and requires TTY.
- Current integration tests already assert strict marker ordering, which is
  useful as a behaviour lock while refactoring internals.
- `main` had drifted from README claims before this change: README documented
  concurrent file checks while code was strictly sequential.
- The full copied benchmark corpus is very large; running full hyperfine passes
  in this environment can exceed practical command-duration limits. A bounded
  benchmark corpus is necessary for routine validation.

## Decision Log

- Decision: use a global diagram task scheduler with semaphore bounds and a
  central deterministic emitter keyed by stable ordering identifiers.
  Rationale: enables both within-file and across-file concurrency while keeping
  output deterministic.
- Decision: keep deterministic order based on original traversal order rather
  than completion order. Rationale: compatibility with existing output
  expectations and tests.
- Decision: add `--max-concurrency` so benchmarks can compare serial and
  bounded-concurrent execution without code changes. Rationale: enables
  performance regression testing and user control while preserving a safe
  default cap.
- Decision: update markdown quality-gate target scope to tracked project docs
  while excluding copied benchmark fixtures and `.rules` reference material.
  Rationale: copied fixtures are external corpora used for benchmarking, not
  style-conforming project documentation.

## Implementation Plan

First, introduce structured diagram outcomes that include ordered identifiers,
marker metadata, success flag, and any stderr message payload to emit. Refactor
worker paths to return outcomes instead of printing diagnostic text directly.

Second, create a scheduler in `main` that:

1. Discovers files in deterministic order.
2. Parses each file to discover diagrams and allocate stable global ordinals.
3. Submits one async task per diagram guarded by a global semaphore with
   `max(1, cpu_count - 1)`.
4. Consumes completions with `asyncio.as_completed`, storing outcomes in a
   pending map and emitting only the next expected ordinal to keep
   deterministic stream order.
5. Handles files with zero diagrams by emitting boundaries in deterministic
   order and preserving exit semantics.

Third, add tests:

- Unit tests for concurrency-limit calculation and ordered-emitter behaviour.
- Integration tests proving deterministic output despite out-of-order task
  completion and mixed failures.
- pytest-bdd scenarios validating ordered streaming and stable boundaries.

Fourth, copy provided external markdown docs into
`tests/fixtures/benchmark_docs/` preserving project-segmented paths, then add a
benchmark helper/target using `hyperfine` to compare `--max-concurrency 1`
against default bounded concurrency.

Fifth, update docs and metadata: add `docs/nixie-design.md`, update README
concurrency wording, and bump Python requirement to 3.14.

Finally, run all gates with `tee` logs and commit.

## Validation & Observability

Run the following and inspect outputs/logs:

    make check-fmt 2>&1 | tee /tmp/check-fmt-nixie-$(git branch --show).out
    make lint 2>&1 | tee /tmp/lint-nixie-$(git branch --show).out
    make typecheck 2>&1 | tee /tmp/typecheck-nixie-$(git branch --show).out
    make test 2>&1 | tee /tmp/test-nixie-$(git branch --show).out
    make markdownlint 2>&1 | tee /tmp/markdownlint-nixie-$(git branch --show).out
    make nixie 2>&1 | tee /tmp/nixie-nixie-$(git branch --show).out

For benchmark validation (post-refactor):

    hyperfine \
      'uv run nixie tests/fixtures/benchmark_docs --max-concurrency 1' \
      'uv run nixie tests/fixtures/benchmark_docs'

Expected observable results:

- Output markers remain deterministic and correctly nested.
- Exit code remains 0 for all-valid inputs and 1 when at least one diagram
  fails.
- Tests include pytest-bdd scenarios and pass.
- Benchmark indicates improved wall-clock time for bounded concurrency vs
  single-worker mode on multi-core hosts.

## Outcomes & Retrospective

Nixie now runs diagram validation concurrently across and within files while
preserving deterministic marker order. A bounded worker cap is enforced through
`--max-concurrency` clamped to `max(1, cpu_count - 1)`.

Coverage now includes:

- unit tests for concurrency resolution and timeout payload behaviour
- integration tests for deterministic ordering under out-of-order completion
- pytest-bdd scenario coverage for ordered streaming and failure diagnostics

Benchmarking support is now part of the repository workflow via
`make benchmark` (hyperfine). In this environment, the benchmark sample corpus
showed a 4.41x speedup for bounded-concurrency mode vs serial mode.

The largest practical lesson: copied external markdown corpora are useful for
realistic benchmarking but too large for routine markdown style gates. Project
markdown gates were scoped to tracked project docs while excluding benchmark
fixture corpora.
