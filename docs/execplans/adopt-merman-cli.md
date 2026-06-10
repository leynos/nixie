# Adopt merman-cli as nixie's preferred Mermaid renderer

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

nixie validates Mermaid diagrams embedded in Markdown files by writing each
fenced `mermaid` block to a temporary `.mmd` file, shelling out to a renderer,
and treating a non-zero exit status as a validation failure. Today the only
renderer is `mmdc`, the official `@mermaid-js/mermaid-cli`, which requires a
Node.js or Bun runtime and launches a headless Chromium browser via Puppeteer.

After this change, nixie prefers **merman-cli**, a headless Rust
re-implementation of Mermaid (<https://github.com/Latias94/merman>, published
on crates.io as `merman-cli`, version 0.7.0 at the time of writing, MSRV Rust
1.95). merman-cli renders Mermaid to SVG natively with no Node, no npm
packages, and no Chromium. A user with `merman-cli` installed can run `nixie`
with zero JavaScript tooling on the machine.

This is a **renderer migration with a soft landing**, not a blind executable
rename. A new `--renderer {auto,merman,mmdc}` flag selects the backend.
The default, `auto`, prefers `merman-cli` and falls back to the existing
`mmdc`/`bun`/`npx` discovery chain when `merman-cli` is absent. Removing the
Node/Puppeteer machinery entirely is a deliberate follow-up (a second PR,
out of scope here) once real-world parity deltas have been observed.

Observable success: on a machine with `merman-cli` on `PATH` and no Node
tooling, `uv run nixie README.md` exits 0 and `uv run nixie --verbose
README.md` logs a command of the form `merman-cli -i …​.mmd -o …​.svg`. On a
machine with only Node tooling, behaviour is unchanged from today. `--renderer
merman` on a machine without `merman-cli` fails fast with a clear error.

## Constraints

Hard invariants that must hold throughout implementation. Violation requires
escalation, not workarounds.

- The existing `mmdc`/`bun`/`npx` path must keep working in this release.
  `--renderer mmdc` must reproduce today's behaviour exactly, and `auto`
  without `merman-cli` installed must be indistinguishable from today.
- Validation stays render-based. nixie renders each block to SVG; it must not
  switch to `merman-cli parse` because "parses" is a weaker guarantee than
  "renders".
- The `nixie` console-script interface remains backwards compatible: all
  existing flags (`--verbose`, `--no-sandbox`, `--mermaid-version`,
  `--max-concurrency`, positional paths) continue to be accepted.
  `--no-sandbox` and `--mermaid-version` apply only to the mmdc backend and
  are inert (but not errors) under merman.
- Never pass `--puppeteerConfigFile` to `merman-cli`. It exists upstream as a
  compatibility shim, but merman does not launch Chromium and relying on the
  shim couples nixie to unverified behaviour.
- The single-module architecture (`nixie/cli.py`) is preserved; this plan
  does not split the package.
- The executable allow-list security control (`ALLOWED_EXECUTABLES` enforced
  in `_run_mermaid_cli`) must remain in force; `merman-cli` is added to it,
  nothing is removed from the enforcement path.
- Python `>=3.14` (per `pyproject.toml`); all gates (`make check-fmt`,
  `make typecheck`, `make lint`, `make test`, and `make markdownlint` /
  `make nixie` for Markdown) must pass before each commit.
- New runtime dependencies are forbidden. New **dev** dependencies are limited
  to `syrupy` and `hypothesis` (pre-authorised by the change request).

## Tolerances (exception triggers)

- Scope: if implementation requires changes beyond `nixie/cli.py`, the test
  trees (`nixie/unittests/`, `tests/`), `pyproject.toml` (dev deps only),
  `uv.lock`, the `Makefile`, and the documentation set named in this plan,
  stop and escalate.
- Interface: if an existing public flag must change meaning (rather than gain
  a sibling), stop and escalate.
- Dependencies: any new dependency beyond the two pre-authorised dev
  dependencies triggers escalation.
- Iterations: if a gate still fails after 3 focused attempts at the same
  failure, stop and escalate.
- Upstream drift: if `merman-cli`'s actual flag surface contradicts what this
  plan records (e.g. `-i`/`-o` root-level mode does not behave as documented),
  stop, record the observation in `Surprises & Discoveries`, and escalate.
- Review: if `coderabbit review --agent` raises a concern that conflicts with
  a constraint above, stop and escalate rather than resolving unilaterally.

## Risks

- Risk: merman is an independent compatibility implementation targeting
  Mermaid 11.15.0, not the official renderer; diagrams accepted by one may be
  rejected by the other.
  Severity: medium. Likelihood: medium.
  Mitigation: `auto` retains the mmdc fallback; the README and users' guide
  state the validation-authority change explicitly; full removal of mmdc is
  deferred to a second PR after fixture comparison.
- Risk: merman-cli's stderr error formatting is unverified (its `CliError`
  variants are known — `Io`, `Mermaid`, `Headless`, `Raster`, `Json`,
  `NoDiagram`, `InvalidInput`, `InvalidOutput` — but the exact text is not).
  Severity: low. Likelihood: high.
  Mitigation: `format_cli_error` already falls back to raw stripped stderr
  when the mmdc `Parse error on line N:` pattern is absent; snapshot tests pin
  **nixie's** formatting contract, not merman's output.
- Risk: CI machines and contributor machines will mostly lack `merman-cli`,
  so the merman path could go untested in practice.
  Severity: medium. Likelihood: high.
  Mitigation: unit/behavioural tests stub discovery (`shutil.which`,
  candidate paths) exactly as the existing mmdc tests do, so both backends are
  exercised regardless of what is installed.
- Risk: threading a renderer choice through `main` → `_run_diagram_task` →
  `_render_diagram` → command builder widens several signatures and could
  ripple through many tests.
  Severity: low. Likelihood: medium.
  Mitigation: resolve the renderer **once** in `main` (a `ResolvedRenderer`
  value) and pass that single object down, keeping per-call signatures stable.

## Progress

- [x] (2026-06-09 12:30Z) Explored codebase seam, test inventory, docs
  inventory, and merman upstream facts (parallel agent sweep).
- [x] (2026-06-09 12:40Z) ExecPlan drafted.
- [x] (2026-06-09 14:00Z) Plan approved by user; implementation authorized.
- [x] (2026-06-09 14:20Z) Precursor: repaired the broken typecheck baseline
  (`ty 0.0.32` drift; see Surprises) so all four gates pass before work
  begins. Commit `15fe2f3`.
- [x] (2026-06-09 15:00Z) Milestone 1: renderer selection seam
  (red → green → refactor). All gates pass (156 tests). Manual checks:
  `--help` documents `--renderer`; `--renderer merman` without the binary
  exits 1 with the install-route message.
- [x] (2026-06-09 15:40Z) Milestone 2: error formatting, snapshot tests,
  property tests. `syrupy` and `hypothesis` added as dev dependencies; five
  snapshots recorded and reviewed; six properties pass. All gates pass
  (167 tests).
- [x] (2026-06-09 16:20Z) Milestone 3: behavioural (pytest-bdd) and
  end-to-end coverage. Four BDD scenarios pass; a deliberate mutation
  (auto preferring mmdc) was caught by the auto-prefers-merman scenario and
  then reverted; five end-to-end tests drive the real `cli()` entry point;
  the Windows-shim integration test now covers `merman-cli` shims. All
  gates pass (179 tests).
- [x] (2026-06-09 17:00Z) Milestone 4: documentation. README requirements
  and flag docs updated; CHANGELOG entries added; `docs/users-guide.md` and
  `docs/developers-guide.md` created;
  `docs/adr/0001-prefer-merman-cli-over-mmdc.md` created and linked from the
  design doc; `docs/nixie-design.md` gained a Renderer Backends section and
  updated failure list; `docs/diagram-processing.md` notes the selectable
  Renderer participant. `make markdownlint` and `make nixie` pass (the
  latter validated the docs end-to-end through the real `auto` fallback on
  a merman-less machine). All code gates pass (179 tests).
- [x] (2026-06-10 00:10Z) Milestone 5: full gate suite passes end to end
  (check-fmt, typecheck, lint, 179 tests, markdownlint, `make nixie`);
  CodeRabbit reviewed every milestone with zero findings (the Milestone 4
  review hit the free-tier rate limit once and was retried after a pause);
  retrospective completed below.

## Surprises & discoveries

- Observation: `docs/users-guide.md` and `docs/developers-guide.md` do not
  exist; the repository documents user-facing behaviour in `README.md` and
  architecture in `docs/nixie-design.md`.
  Evidence: `ls docs/` shows only `CHANGELOG.md`, `diagram-processing.md`,
  `execplans/`, `nixie-design.md`.
  Impact: Milestone 4 creates both guides rather than editing them.
- Observation: no ADR directory or ADR convention exists in the repository.
  Evidence: same directory listing; no `docs/adr/` and no ADR references in
  `docs/`.
  Impact: Milestone 4 creates `docs/adr/0001-prefer-merman-cli-over-mmdc.md`
  using the Y-Statement-plus-context format and links it from
  `docs/nixie-design.md`.
- Observation: `syrupy` and `hypothesis` are not yet dev dependencies; the
  test stack is `pytest`, `pytest-asyncio`, `pytest-bdd`.
  Evidence: `pyproject.toml` `[dependency-groups] dev`.
  Impact: both are added (dev-only) in Milestones 2; recorded as a tolerated
  dependency addition.
- Observation: the baseline `make typecheck` gate was already broken at the
  branch point — `ty 0.0.32` reports three diagnostics: it cannot see through
  pytest's `_WithException` protocol wrapper (so valid `pytest.skip` /
  `pytest.fail` calls are rejected in `tests/integration/test_packaging.py`
  and `tests/integration/test_windows_executables.py`), and it rejects the
  `pathspec` `SimpleNamespace` shim assignment in `nixie/cli.py`.
  Evidence: `make typecheck` at `3cd9880` exits 1 with those three
  diagnostics; `make test` passes 127 tests, so the code is correct.
  Impact: fixed in a precursor commit before Milestone 1 (keyword `reason=`
  arguments plus narrowly scoped `# ty: ignore[...]` comments explaining the
  checker limitation) so every later milestone gates against a green
  baseline.

## Decision log

- Decision: implement a `--renderer {auto,merman,mmdc}` switch with `auto` as
  default, rather than a hard cut-over to `merman-cli`.
  Rationale: merman is a compatibility implementation, not the official
  renderer; a fallback release lets CI users compare failure deltas before
  the Node path is removed in a follow-up PR.
  Date/Author: 2026-06-09, planning session.
- Decision: keep `--no-sandbox` and `--mermaid-version` accepted but inert
  under the merman backend (documented, not warned-as-error).
  Rationale: backwards compatibility for existing CI invocations; both flags
  remain meaningful whenever `auto` falls back to mmdc on the same machine.
  Date/Author: 2026-06-09, planning session.
- Decision: resolve the renderer once in `main` into a small frozen dataclass
  (`ResolvedRenderer`) instead of re-running discovery per diagram.
  Rationale: discovery touches the filesystem; per-diagram repetition is
  wasteful under the bounded concurrent scheduler and makes logs noisy; a
  single resolution also lets Puppeteer config creation become conditional.
  Date/Author: 2026-06-09, planning session.
- Decision: create `docs/users-guide.md`, `docs/developers-guide.md`, and
  `docs/adr/0001-prefer-merman-cli-over-mmdc.md` as new documents.
  Rationale: the change request requires these documents to be updated; they
  do not exist, so minimal versions are created covering the renderer change
  and pointing back to existing material rather than duplicating it.
  Date/Author: 2026-06-09, planning session.
- Decision: demonstrate the Milestone 1 red stage via import-time failures
  rather than strict-xfail markers.
  Rationale: the new tests import seam symbols (`NoRendererAvailableError`,
  `ResolvedRenderer`, `find_merman_cli`, …) that do not exist before the
  green step, so pytest fails at collection; `xfail(strict=True)` never
  executes for a module that cannot import. The ImportError transcript names
  the exact unimplemented symbols, which is equivalent red evidence.
  Date/Author: 2026-06-09, Milestone 1.
- Decision: existing test stubs for `shutil.which` and `_render_diagram`
  were made renderer-aware (name-sensitive `which` fakes; stubs accept a
  keyword-only `renderer` argument) rather than freezing `_render_diagram`'s
  old signature.
  Rationale: blanket `which` fakes that returned `mmdc` for *any* lookup
  would silently resolve the merman backend under `auto`, changing what the
  tests exercise; name-sensitive fakes pin each test to its intended
  backend.
  Date/Author: 2026-06-09, Milestone 1.
- Decision: leave the `.hypothesis/` example database out of version control.
  Rationale: the hypothesis skill recommends committing
  `.hypothesis/examples`, but the repository's `.gitignore` already excludes
  `.hypothesis/` (an explicit prior convention) and no failing seeds exist
  to preserve; repository convention wins. Shrunk failures, if any arise,
  are promoted to named unit tests instead.
  Date/Author: 2026-06-09, Milestone 2.
- Decision: property tests use `unittest.mock.patch` context managers rather
  than pytest's `monkeypatch`/`tmp_path` fixtures, with purely symbolic
  (non-filesystem) paths.
  Rationale: Hypothesis re-runs the test body many times per
  function-scoped fixture instance and flags such fixtures with a health
  check; symbolic paths plus per-example `mock.patch` keep every example
  hermetic without suppressing health checks.
  Date/Author: 2026-06-09, Milestone 2.
- Decision: keep rendering to SVG (`-i in.mmd -o out.svg`) for both backends;
  do not adopt `merman-cli parse`.
  Rationale: nixie's contract is "this block renders", which subsumes
  parsing; merman's root-level mmdc-compatible mode maps directly onto the
  existing invocation shape.
  Date/Author: 2026-06-09, planning session.

## Outcomes & retrospective

Outcome: delivered as planned. nixie now prefers `merman-cli` under the
default `--renderer auto`, falls back to the unchanged `mmdc`/`bun`/`npx`
chain, fails fast with an install hint when `merman` is forced without the
binary, and only generates Puppeteer configuration for the mmdc backend.
Every constraint held: no runtime dependencies were added, the legacy
behaviour is byte-for-byte preserved (`get_mmdc_cmd` untouched; delegation
covered by an exact-equality test), validation remains render-based, and
the allow-list enforcement gained `merman-cli` without weakening.

Compared with the original purpose: all observable-success criteria are
met and test-verified — the merman command shape, the fallback, the
fail-fast error, and the inertness of `--no-sandbox`/`--mermaid-version`
under merman. After `merman-cli` 0.7.0 was installed on the development
machine (2026-06-10), the remaining criterion was demonstrated live: a
real diagram rendered through merman in auto mode (exit 0), a genuinely
invalid block surfaced merman's real stderr verbatim (exit 1), and the
same inputs agreed with the forced mmdc backend — see `Artifacts and
notes` for transcripts.

Coverage delivered: 52 new or updated tests (179 total, up from 127) across
unit, snapshot (syrupy), property (Hypothesis), behavioural (pytest-bdd),
and end-to-end layers, plus a deliberate-mutation check proving the BDD
suite catches auto-preference regressions.

Lessons learned:

- The baseline gates were broken before work began (`ty` drift); gating the
  baseline first made every later red observation trustworthy.
- Blanket test fakes (a `shutil.which` stub answering every lookup) are a
  trap when adding discovery for a new executable: they silently change
  which backend a test exercises. Name-sensitive fakes are now the
  documented convention.
- The first Hypothesis run caught a bug in its own strategy (an impossible
  backend/tool pairing), a useful reminder to audit strategies before
  trusting green properties.
- Import-time failures are the honest red signal when the test imports the
  symbols it specifies; strict-xfail cannot express that stage.

Follow-up (the second PR, out of scope here): fixture-corpus comparison of
merman vs mmdc acceptance, then removal of the Node/Puppeteer machinery as
recorded in ADR 0001.

## Context and orientation

The whole renderer lives in one module: `nixie/cli.py`. Line numbers below
refer to the state at commit `fbb77a0`.

Terms used in this plan:

- **mmdc** — the executable installed by `@mermaid-js/mermaid-cli`, the
  official Node.js Mermaid renderer. It drives a headless Chromium browser
  through **Puppeteer**, a Node browser-automation library, which is why nixie
  generates a temporary Puppeteer JSON config today.
- **merman / merman-cli** — a Rust re-implementation of Mermaid
  (<https://github.com/Latias94/merman>). The `merman-cli` binary renders
  `.mmd` files to SVG (and other formats) headlessly. Installed via
  `cargo install merman-cli` or prebuilt GitHub release binaries. Its root
  command accepts mmdc-style `-i input.mmd -o output.svg`; it also has
  `detect`/`parse`/`layout`/`render` subcommands, which this plan does not
  use. Exit code is 0 on success and 1 on any error.
- **renderer seam** — the narrow choke-point where nixie builds and runs the
  external command.
- **allow-list** — a security control: before spawning a subprocess, nixie
  normalizes `cmd[0]` and rejects executables whose base name is not in
  `ALLOWED_EXECUTABLES`.

Key code (all in `nixie/cli.py`):

- `ALLOWED_EXECUTABLES` (line 97): `frozenset({"mmdc", "bun", "npx"})`.
- `WINDOWS_EXECUTABLE_SUFFIXES` (line 98): `(".exe", ".cmd", ".bat")`,
  stripped by `_normalize_executable_name` (lines 450–466) before
  `_is_allowed_executable` (lines 469–474) checks membership.
- `DEFAULT_PUPPETEER_ARGS` (lines 100–104), `create_puppeteer_config`
  (lines 335–360): context manager yielding a temp JSON config; appends
  `--no-sandbox` when forced via flag or when running as root.
- `DEFAULT_MERMAID_VERSION` / `MERMAID_CLI_PACKAGE` (lines 108–109) and
  `_resolve_mermaid_cli_package` (lines 363–368): build the
  `@mermaid-js/mermaid-cli@<version>` spec for `npx`/`bun` launches.
- `get_mmdc_cmd` (lines 371–419): discovery order `~/.bun/bin/mmdc` →
  `./node_modules/.bin/mmdc` → `~/.npm-global/bin/mmdc` →
  `shutil.which("mmdc")` → `shutil.which("bun")` → `shutil.which("npx")`;
  raises `NoNodeEnvironmentAvailableError` (lines 157–161) when nothing is
  found; appends `--puppeteerConfigFile <cfg>` then `-i <mmd> -o <svg>`.
- `format_cli_error` (lines 422–432): extracts mmdc's
  `Parse error on line N:` snippet (three following lines) or falls back to
  stripped stderr.
- `_run_mermaid_cli` (lines 477–493): enforces the allow-list
  (`UnexpectedExecutableError`, lines 150–154) and spawns the subprocess.
- `_render_diagram` (lines 496–545): writes the `.mmd`, builds the command,
  logs it via `shlex.join`, raises `RuntimeError` with
  `format_cli_error(stderr)` on failure.
- `parse_args` (lines 844–889) and `cli` (lines 892–918): argparse flags
  `--verbose`, `--no-sandbox`, `--mermaid-version`, `--max-concurrency`;
  `main` creates the Puppeteer config once and fans diagrams out through
  `_run_diagram_task` → `_render_diagram`.

Key tests:

- `nixie/unittests/test_get_mmdc_cmd.py` — discovery order, `bun x --bun` /
  `npx --yes` argument shapes, version injection, non-executable skipping,
  `NoNodeEnvironmentAvailableError`.
- `nixie/unittests/test_cli_executable_allowlist.py` — normalization of
  POSIX/Windows paths and `.exe`/`.cmd`/`.bat` suffixes; accept/reject sets.
- `nixie/unittests/test_render_diagram.py` — error message contains the
  joined command and `mmdc`; logging of the command; Windows-suffix
  acceptance in `_run_mermaid_cli`.
- `nixie/unittests/test_puppeteer_config.py` — config generation.
- `tests/integration/test_windows_executables.py` — end-to-end Windows shim
  discovery via `shutil.which`; rejection of non-allow-listed executables.
- `tests/integration/test_cli_behavior.py`, `tests/integration/
  test_no_args.py` — end-to-end behaviour through `main`.
- `tests/bdd/features/deterministic_order.feature` +
  `tests/bdd/test_deterministic_order_bdd.py` — the existing pytest-bdd
  pattern to copy (feature files under `tests/bdd/features/`, steps in a
  sibling `test_*_bdd.py` using `scenarios(...)`).

Key documentation:

- `README.md` — requirements (currently "Node.js with `npx` or Bun"), flag
  documentation, Puppeteer notes.
- `docs/nixie-design.md` — architecture; error-handling section lists
  "missing node runtime (`mmdc`, `bun`, `npx`)".
- `docs/diagram-processing.md` — Mermaid sequence diagram of the CLI →
  Renderer flow.
- `docs/CHANGELOG.md` — keep-a-changelog style with an Unreleased section.

Tooling: `make test` (`uv run pytest -v`), `make lint` (`uv run ruff check`),
`make typecheck` (`uv run ty check`), `make check-fmt`
(`uv run ruff format --check`), `make markdownlint`, `make nixie`
(self-validation of docs' Mermaid diagrams).

Relevant skills for the implementer: `leta` (load at session start; use
`leta show`, `leta refs`, `leta calls` to navigate `nixie/cli.py` instead of
reading the whole 900-line module), `python-router` (route deeper questions),
`python-testing` (fixture and parametrization patterns), `hypothesis`
(property-test strategy design for Milestone 2),
`python-errors-and-logging` (exception shape for the new error type),
`commit-message` and `changelog` (Milestone 4/5 hygiene). Repository
conventions live in `AGENTS.md` and `.rules/python-*.md`.

## Plan of work

### Milestone 1 — renderer selection seam

All edits in `nixie/cli.py`; tests first.

Stage B (red): create `nixie/unittests/test_get_renderer_cmd.py` covering the
new behaviour below, plus targeted edits to the four existing test files.
Mark genuinely new tests `@pytest.mark.xfail(strict=True, reason="renderer
seam not yet implemented")`, run `make test` to observe the strict-xfail
failures, then remove the markers as each green step lands.

Stage C (green), in dependency order:

1. Constants: add `MERMAN_EXECUTABLE: typ.Final[str] = "merman-cli"`; extend
   `ALLOWED_EXECUTABLES` to `frozenset({"mmdc", "bun", "npx", "merman-cli"})`.
2. Renderer choice type: `RendererChoice = typ.Literal["auto", "merman",
   "mmdc"]` plus a `RENDERER_CHOICES: typ.Final[tuple[str, ...]]` for
   argparse.
3. New exception `NoRendererAvailableError(RuntimeError)` with message
   "No Mermaid renderer available. Install merman-cli (cargo install
   merman-cli) or a Node environment with @mermaid-js/mermaid-cli.".
   `NoNodeEnvironmentAvailableError` remains for the forced-mmdc path.
4. Discovery: `find_merman_cli() -> str | None` checking, in order,
   `~/.cargo/bin/merman-cli` (file + executable bit, mirroring the existing
   candidate-path idiom in `get_mmdc_cmd`) then `shutil.which("merman-cli")`.
5. Resolution: a frozen dataclass `ResolvedRenderer` with fields
   `backend: typ.Literal["merman", "mmdc"]` and `needs_puppeteer_config:
   bool` (True only for mmdc), and a function
   `resolve_renderer(choice: RendererChoice) -> ResolvedRenderer`:
   `merman` → merman or raise `NoRendererAvailableError`; `mmdc` → mmdc
   (existing discovery raises `NoNodeEnvironmentAvailableError` later, as
   today); `auto` → merman if `find_merman_cli()` succeeds, else mmdc.
6. Command building: `get_merman_cmd(mmd: Path, svg: Path) -> list[str]`
   returning `[cli, "-i", str(mmd), "-o", str(svg)]` (raising
   `NoRendererAvailableError` if discovery fails — covers a binary vanishing
   between resolution and use), and a dispatching
   `get_renderer_cmd(mmd, svg, cfg_path, *, renderer: ResolvedRenderer,
   mermaid_version: str = DEFAULT_MERMAID_VERSION) -> list[str]` that calls
   `get_merman_cmd` or `get_mmdc_cmd`. `get_mmdc_cmd` itself is unchanged.
7. Threading: `_render_diagram` and `_run_diagram_task` gain a
   `renderer: ResolvedRenderer` parameter and call `get_renderer_cmd`;
   `main` gains `renderer: RendererChoice = "auto"`, calls
   `resolve_renderer` once, and only enters `create_puppeteer_config` when
   `resolved.needs_puppeteer_config` is true (passing `cfg_path=None`
   otherwise); `parse_args` gains
   `--renderer` with `choices=RENDERER_CHOICES, default="auto"`; `cli`
   forwards `parsed.renderer`.
8. `_run_mermaid_cli` is renamed in docstring intent only (it already takes a
   generic `cmd`); no signature change.

Stage D (refactor): update module docstring and any comments that say
"mermaid-cli" where "the selected renderer" is now accurate; run all gates;
`coderabbit review --agent`; commit.

Test edits in this milestone:

- `test_get_mmdc_cmd.py`: unchanged in substance (mmdc builder is intact);
  add an assertion that `get_renderer_cmd` with a forced-mmdc
  `ResolvedRenderer` delegates to it byte-for-byte.
- `test_cli_executable_allowlist.py`: extend the parametrized accept cases
  with `merman-cli`, `merman-cli.exe`, `merman-cli.cmd`, `merman-cli.bat`,
  and a POSIX path `/home/user/.cargo/bin/merman-cli`; keep all reject cases.
- `test_render_diagram.py`: the failure-message test parametrizes over both
  backends; for merman it asserts the message contains `merman-cli` and the
  raw stderr fallback text.
- `test_get_renderer_cmd.py` (new): auto-prefers-merman; auto-falls-back;
  forced merman raises `NoRendererAvailableError` when absent; merman command
  shape is exactly `[cli, "-i", mmd, "-o", svg]` (no
  `--puppeteerConfigFile`, no version spec); `~/.cargo/bin` candidate wins
  over `PATH`; non-executable candidate is skipped. Reuse the `fake_home_cwd`
  fixture from `nixie/unittests/conftest.py` and the
  `monkeypatch.setattr(shutil, "which", ...)` idiom from
  `test_get_mmdc_cmd.py`.

### Milestone 2 — error formatting, snapshots, properties

Stage B (red): add `syrupy` and `hypothesis` to
`[dependency-groups] dev` in `pyproject.toml` (`uv lock` to refresh
`uv.lock`), then write the failing tests.

- `nixie/unittests/test_format_cli_error.py` (new, or extend where the
  existing coverage lives): syrupy snapshot tests pinning nixie's formatted
  output for (a) an mmdc `Parse error on line N:` block, (b) a merman-style
  plain stderr message (e.g. `Mermaid error: …`), and (c) the full
  `_render_diagram` RuntimeError message shape for each backend. Snapshots
  live in syrupy's default `__snapshots__/` directory beside the test.
  These exist because the error text is nixie's user-visible output format
  and must not drift silently.
- `nixie/unittests/test_renderer_properties.py` (new), Hypothesis properties:
  - `_normalize_executable_name` is idempotent, case-insensitive on output,
    and strips exactly one known Windows suffix, for generated mixes of path
    separators, case, whitespace, and suffixes.
  - `_is_allowed_executable` is False for any generated name whose
    normalized base is outside `ALLOWED_EXECUTABLES`, however decorated with
    directories and known suffixes (the allow-list cannot be bypassed by
    path or suffix games).
  - `get_renderer_cmd` invariant: for arbitrary valid temp paths and both
    backends, the result ends `["-i", str(mmd), "-o", str(svg)]` and
    `_is_allowed_executable(result[0])` is True.

Stage C (green): `format_cli_error` likely needs no production change (its
fallback already handles merman); if snapshots reveal a gap (e.g. trailing
noise), make the minimal fix. Stage D: gates, CodeRabbit, commit.

### Milestone 3 — behavioural and end-to-end coverage

Feature specification, `tests/bdd/features/renderer_selection.feature`:

```gherkin
Feature: Renderer selection
  nixie chooses between merman-cli and the Node-based mermaid-cli.

  Scenario: Auto mode prefers merman-cli when it is installed
    Given merman-cli is installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie in auto renderer mode
    Then the diagram is rendered with merman-cli
    And no Puppeteer configuration file is created

  Scenario: Auto mode falls back to mmdc when merman-cli is absent
    Given merman-cli is not installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie in auto renderer mode
    Then the diagram is rendered with the Node-based mermaid-cli

  Scenario: Forcing merman without merman-cli fails clearly
    Given merman-cli is not installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie forcing the merman renderer
    Then validation fails before any diagram is rendered
    And the error names merman-cli and how to install it

  Scenario: Forcing mmdc preserves the legacy pipeline
    Given merman-cli is installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie forcing the mmdc renderer
    Then the diagram is rendered with the Node-based mermaid-cli
```

Steps in `tests/bdd/test_renderer_selection_bdd.py`, following the
`deterministic_order` pattern: a `ScenarioState` dict, stubbed discovery
(monkeypatched `shutil.which` / candidate paths), a recording stub on
`asyncio.create_subprocess_exec` capturing `cmd[0]`, and invocation through
`await main(paths, renderer=…)`.

End-to-end and integration updates:

- `tests/integration/test_windows_executables.py`: parametrize the accepted
  shim names over `merman-cli.EXE`/`.CMD`/`.BAT` alongside the mmdc cases.
- `tests/integration/test_cli_behavior.py` (or a new
  `tests/integration/test_renderer_selection.py`): drive `parse_args` +
  `main` with `--renderer` in all three modes against stubbed discovery,
  asserting exit codes and stderr; verify `--renderer auto` on a
  "nothing installed" machine reports `NoRendererAvailableError`'s message
  and exits non-zero; verify `--no-sandbox` with merman resolved does not
  create a Puppeteer config (assert via monkeypatched
  `create_puppeteer_config` or filesystem observation).

Stage D: gates, CodeRabbit, commit.

### Milestone 4 — documentation

- `README.md`: Requirements section becomes "merman-cli (recommended;
  `cargo install merman-cli` or a release binary) **or** Node.js with `npx`
  / Bun with `@mermaid-js/mermaid-cli`"; document `--renderer` and note that
  `--no-sandbox` / `--mermaid-version` apply only to the mmdc backend; state
  plainly that merman is an independent Rust implementation targeting
  Mermaid 11.15.0, so acceptance may differ at the margins from the official
  renderer, and that `--renderer mmdc` restores the previous authority.
- `docs/users-guide.md` (new): CLI usage guide — invocation, file discovery,
  every flag with its backend applicability, renderer installation for both
  backends, exit codes, and error-output examples for each backend.
- `docs/developers-guide.md` (new): internal conventions — the renderer seam
  (`resolve_renderer` / `get_renderer_cmd` / `_run_mermaid_cli`), the
  executable allow-list practice and how to extend it safely, the test
  layering (unit in `nixie/unittests/`, integration and BDD under `tests/`),
  the snapshot and property-test conventions introduced in Milestone 2, and
  the quality gates.
- `docs/adr/0001-prefer-merman-cli-over-mmdc.md` (new): records the change of
  validation authority from `@mermaid-js/mermaid-cli` to merman-with-fallback,
  the alternatives considered (hard cut-over; parse-only validation; staying
  on mmdc), and the planned second-phase removal of the Node path.
- `docs/nixie-design.md`: update the error-handling list (renderer
  unavailability now has two shapes) and add a short "Renderer backends"
  section referencing the ADR.
- `docs/diagram-processing.md`: the sequence diagram's `Renderer` participant
  gains a note that it is merman-cli or mmdc selected at startup.
- `docs/CHANGELOG.md`: Unreleased entries — Added `--renderer`; Added
  merman-cli support (preferred under `auto`); Changed default renderer
  preference; Deprecated reliance on the implicit Node path (removal planned
  for a future release).

Gate with `make markdownlint` and `make nixie` (the docs themselves contain
Mermaid); then gates, CodeRabbit, commit.

### Milestone 5 — final verification and retrospective

Run the full gate suite end to end, run `coderabbit review --agent` once more
over the whole branch, clear any concerns, update `Progress`, complete
`Outcomes & Retrospective`, and ensure every commit message follows
`AGENTS.md` conventions.

### Explicitly out of scope (the second PR)

Deleting `MERMAID_CLI_PACKAGE`, `_resolve_mermaid_cli_package`,
`--mermaid-version`, the `bun`/`npx` fallback, `create_puppeteer_config`,
`DEFAULT_PUPPETEER_ARGS`, and `--no-sandbox`; shrinking
`ALLOWED_EXECUTABLES` to `{"merman-cli"}`. Precondition: a fixture-corpus
comparison of merman vs mmdc acceptance over this repository's documentation
set and any user-reported deltas after one release with `auto`.

## Concrete steps

All commands run from the repository root. Per workspace convention, pipe
long outputs through `tee` to `/tmp`:

```bash
BRANCH="$(git branch --show-current | tr '/' '-')"
make test 2>&1 | tee "/tmp/test-nixie-${BRANCH}.out"
make lint 2>&1 | tee "/tmp/lint-nixie-${BRANCH}.out"
make typecheck 2>&1 | tee "/tmp/typecheck-nixie-${BRANCH}.out"
make check-fmt 2>&1 | tee "/tmp/check-fmt-nixie-${BRANCH}.out"
```

Baseline before any edit: run all four; all must pass (they do at
`fbb77a0`). For focused red/green cycles:

```bash
uv run pytest nixie/unittests/test_get_renderer_cmd.py -v
uv run pytest tests/bdd/test_renderer_selection_bdd.py -v
uv run pytest nixie/unittests/test_renderer_properties.py -v
```

Adding dev dependencies (Milestone 2):

```bash
uv add --dev syrupy hypothesis
uv lock
```

Snapshot regeneration when intentionally changing the error format:

```bash
uv run pytest nixie/unittests/test_format_cli_error.py --snapshot-update
```

Markdown gates (Milestone 4):

```bash
make markdownlint
make nixie
```

Commit after every green milestone (and intermediate green steps where
sensible), messages per `AGENTS.md` (imperative subject ≤ 50 chars, wrapped
body explaining why). Then:

```bash
coderabbit review --agent
```

Resolve all concerns before starting the next milestone.

## Validation and acceptance

Red–Green–Refactor evidence to record per milestone (update this section as
work proceeds):

- Milestone 1 red (observed 2026-06-09 14:40Z): the focused run failed for
  the expected reason — `ImportError: cannot import name
  'NoRendererAvailableError' from 'nixie.cli'` (and `ResolvedRenderer` in
  `test_render_diagram.py`), plus six assertion failures in
  `test_cli_executable_allowlist.py` because `merman-cli` was not yet in
  `ALLOWED_EXECUTABLES`. Strict-xfail markers were not usable because the
  missing symbols fail at import (collection) time rather than at test
  execution; the ImportError naming the unimplemented seam serves as the
  red evidence (see Decision Log).
  Green (observed 2026-06-09 14:55Z): `make test` passes with 156 tests
  (127 pre-existing plus the new seam coverage) and zero regressions.
- Milestone 2 red (observed 2026-06-09 15:25Z): all five snapshot tests
  failed with "snapshot does not exist"; snapshots were then recorded with
  `--snapshot-update` and reviewed by eye (concise mmdc parse block, verbatim
  merman stderr, correct per-backend command shapes with `<tmpdir>`
  normalization). The first property run also failed —
  `test_renderer_cmd_invariants` raised `NoNodeEnvironmentAvailableError` —
  which exposed a flaw in the test's own strategy (it could pair the mmdc
  backend with a merman-only machine), not in the production code; the
  strategy now composes only valid backend/tool pairings. Green: all six
  properties and five snapshots pass; `make test` reports 167 passed.
  `format_cli_error` itself needed no production change — its stderr
  fallback already handles merman output, as the plan predicted.
- Milestone 3 red (observed 2026-06-09 16:00Z): all four scenarios in
  `renderer_selection.feature` failed with
  `pytest_bdd.exceptions.StepDefinitionNotFoundError` before the steps were
  wired. After wiring, a deliberate temporary mutation of
  `resolve_renderer` (auto resolving to mmdc even when merman-cli exists)
  made exactly the auto-prefers-merman scenario fail with
  `assert '/usr/bin/mmdc' == '/usr/local/bin/merman-cli'`, proving the
  scenarios detect the regression they exist to catch; the mutation was
  reverted and all four scenarios pass.

Behavioural acceptance:

- With discovery stubbed so `merman-cli` exists: `await main([fixture],
  renderer="auto")` spawns a command whose first element ends in
  `merman-cli` and whose tail is `-i <tmp>.mmd -o <tmp>.svg`; no
  `--puppeteerConfigFile` appears anywhere in the command; no Puppeteer
  config file is created.
- With discovery stubbed so `merman-cli` is absent: identical behaviour to
  today's mmdc path, including the `bun x --bun` / `npx --yes
  @mermaid-js/mermaid-cli@<version>` shapes.
- `nixie --renderer merman` with no `merman-cli` exits non-zero and stderr
  names `merman-cli` and the cargo install command.
- Manual check (where `merman-cli` is installed): `uv run nixie --verbose
  README.md` logs a `merman-cli -i … -o …` line and exits 0.

Quality criteria (what "done" means):

- Tests: `make test` passes; new tests cover both backends and all three
  `--renderer` modes; the BDD feature file's four scenarios pass.
- Lint/typecheck/format: `make lint`, `make typecheck`, `make check-fmt` all
  pass with zero violations.
- Docs: `make markdownlint` and `make nixie` pass over the updated docs.
- Review: `coderabbit review --agent` reports no outstanding concerns at
  each milestone boundary.

## Idempotence and recovery

Every step is re-runnable: tests and gates are read-only, `uv add --dev` and
`uv lock` are idempotent, and snapshot updates are explicit and reviewable in
the diff. Each milestone lands as one or more atomic commits, so recovery
from a bad state is `git revert` (or `git restore` for uncommitted work) at
milestone granularity. No step touches anything outside the repository,
`/tmp` logs, and the shared uv/cargo caches. If `uv lock` conflicts with a
concurrent lock holder, wait for the lock rather than working around it.

## Artifacts and notes

Record, as work proceeds: the red-run transcripts for each milestone, the
reviewed snapshot contents for the error-format tests, and (if a machine
with a real `merman-cli` is available) the transcript of the manual
end-to-end check.

Manual end-to-end verification with a real `merman-cli` (0.7.0 installed at
`~/.cargo/bin/merman-cli`, 2026-06-10):

- Valid diagram, auto mode — merman preferred, renders, exit 0:

```text
$ nixie --verbose good.md
INFO: /home/leynos/.cargo/bin/merman-cli -i …/good_1.mmd -o …/good_1.svg
==> …/good.md
--> line 4: flowchart
<-- line 6: flowchart
<== …/good.md
🧜‍♀️✨ All diagrams validated successfully!
exit=0
```

- Invalid diagram (unknown type) — real merman stderr surfaces verbatim
  through the `format_cli_error` fallback, exit 1:

```text
Error running command /home/leynos/.cargo/bin/merman-cli -i … -o … for
file '…/unknown.md' (diagram 1):
No diagram type detected matching given configuration for text: notadiagram
A-->B
exit=1
```

- Same inputs forced through `--renderer mmdc` (bun-installed `mmdc`, with
  the Puppeteer config in the command) — both backends agreed on every
  tested input, including accepting `flowchart TD\n  A--oops`, so no parity
  drift was observed in this (small) sample.
- `make test` (179 passed) and `make nixie` both pass on the merman-present
  machine, confirming the suite's discovery stubbing keeps tests hermetic
  regardless of what is installed, and that the repository's own
  documentation validates through real merman-cli.

## Interfaces and dependencies

At the end of Milestone 1, `nixie/cli.py` defines:

```python
MERMAN_EXECUTABLE: typ.Final[str] = "merman-cli"
ALLOWED_EXECUTABLES: typ.Final[frozenset[str]] = frozenset(
    {"mmdc", "bun", "npx", "merman-cli"}
)
RendererChoice = typ.Literal["auto", "merman", "mmdc"]
RENDERER_CHOICES: typ.Final[tuple[str, ...]] = ("auto", "merman", "mmdc")


class NoRendererAvailableError(RuntimeError): ...


@dc.dataclass(frozen=True, slots=True)
class ResolvedRenderer:
    backend: typ.Literal["merman", "mmdc"]
    needs_puppeteer_config: bool


def find_merman_cli() -> str | None: ...
def resolve_renderer(choice: RendererChoice) -> ResolvedRenderer: ...
def get_merman_cmd(mmd: Path, svg: Path) -> list[str]: ...
def get_renderer_cmd(
    mmd: Path,
    svg: Path,
    cfg_path: Path | None,
    *,
    renderer: ResolvedRenderer,
    mermaid_version: str = DEFAULT_MERMAID_VERSION,
) -> list[str]: ...
```

`get_mmdc_cmd`, `format_cli_error`, `_normalize_executable_name`,
`_is_allowed_executable`, and `_run_mermaid_cli` keep their current
signatures. `main` gains `renderer: RendererChoice = "auto"`;
`_render_diagram` and `_run_diagram_task` gain `renderer: ResolvedRenderer`.

Dependencies: no runtime additions. Dev additions: `syrupy` (snapshot
fixtures for pytest) and `hypothesis` (property-based testing), both in
`[dependency-groups] dev`. External tool referenced but not vendored:
`merman-cli` ≥ 0.7.0 (crates.io), discovered at runtime, never bundled.

## Revision note

2026-06-10: marked COMPLETE. All five milestones (plus the unplanned
typecheck-baseline precursor) landed with red–green evidence recorded in
`Validation and acceptance`, decisions in the `Decision log`, and findings
in `Surprises & discoveries`. The `Interfaces and dependencies` section
matches the shipped code exactly except that `_render_diagram`'s new
parameter is `renderer: ResolvedRenderer | None = None` (keyword-only,
defaulting to per-call `auto` resolution) to preserve legacy callers — a
narrowing noted in the Decision log. Remaining work is the deferred second
PR (Node/Puppeteer removal after parity comparison), tracked in ADR 0001,
not in this plan.
