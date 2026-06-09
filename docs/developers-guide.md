# Nixie developers' guide

This guide documents internal conventions for contributors. User-facing
behaviour is described in the [users' guide](users-guide.md); the scheduler
architecture lives in [nixie-design.md](nixie-design.md).

## The renderer seam

All rendering flows through a narrow seam in `nixie/cli.py`:

1. `resolve_renderer(choice)` runs **once per invocation** (in `main`) and
   returns a frozen `ResolvedRenderer` dataclass naming the backend
   (`merman` or `mmdc`) and whether a Puppeteer configuration is needed.
   Per-diagram code never re-decides the backend; it receives the resolved
   value. `--renderer merman` without the binary raises
   `NoRendererAvailableError`, which `main` reports and converts to exit 1
   before any diagram is processed.
2. `get_renderer_cmd(mmd, svg, cfg_path, *, renderer, mermaid_version)`
   dispatches to a backend-specific command builder: `get_merman_cmd`
   (exactly `merman-cli -i <mmd> -o <svg>`; never a Puppeteer config or
   version spec) or `get_mmdc_cmd` (the historical mmdc/bun/npx chain).
3. `_run_mermaid_cli(cmd, …)` validates `cmd[0]` against the executable
   allow-list and spawns the subprocess.

When changing renderer behaviour, keep this layering: discovery in
`find_merman_cli`/`get_mmdc_cmd`, policy in `resolve_renderer`, command
shape in the builders, and process control in `_run_mermaid_cli`.

## The executable allow-list

`_run_mermaid_cli` refuses to spawn anything whose normalised base name is
not in `ALLOWED_EXECUTABLES` (`mmdc`, `bun`, `npx`, `merman-cli`).
`_normalize_executable_name` strips directories, one known Windows suffix
(`.exe`, `.cmd`, `.bat`), surrounding whitespace, and case before the check.

To extend the allow-list safely:

1. Add the bare executable name to `ALLOWED_EXECUTABLES`.
2. Extend the accept/reject parametrisations in
   `nixie/unittests/test_cli_executable_allowlist.py`, including Windows
   shim forms.
3. Confirm the Hypothesis properties in
   `nixie/unittests/test_renderer_properties.py` still pass — they assert
   the allow-list cannot be bypassed by path or suffix decoration and that
   every built command starts with an allow-listed executable.

## Test layering

- `nixie/unittests/` — unit tests colocated with the package: discovery,
  resolution, command construction, allow-list normalisation, error
  formatting.
- `tests/integration/` — end-to-end tests driving `main` or the real
  `cli()` entry point with patched `sys.argv`, stubbed discovery, and a
  stubbed subprocess layer.
- `tests/bdd/` — pytest-bdd scenarios; feature files live in
  `tests/bdd/features/` with step modules named `test_<feature>_bdd.py`
  alongside them.

### Stubbing conventions

- Stub `shutil.which` **name-sensitively** (return a path only for the
  executable the scenario installs). A blanket stub that answers every
  lookup will silently resolve the merman backend under `auto` and change
  what the test exercises.
- Patch `nixie.cli.Path.home` to a temporary directory in any test that
  reaches discovery, so a contributor's real `~/.cargo/bin/merman-cli` (or
  `~/.bun/bin/mmdc`) cannot leak in.
- Stubs replacing `_render_diagram` must accept the keyword-only
  `renderer` argument.

### Snapshot tests (syrupy)

`nixie/unittests/test_format_cli_error.py` pins the user-visible error
formatting with [syrupy](https://github.com/syrupy-project/syrupy)
snapshots stored in `__snapshots__/`. Temporary directory paths are
normalised to `<tmpdir>` before snapshotting so the recorded text is
stable. Regenerate intentionally with:

```bash
uv run pytest nixie/unittests/test_format_cli_error.py --snapshot-update
```

Review the resulting `.ambr` diff by eye before committing; the snapshot is
the output contract.

### Property tests (Hypothesis)

`nixie/unittests/test_renderer_properties.py` holds invariants over the
allow-list and command builders. Conventions:

- Compose valid inputs in strategies instead of filtering with `assume`.
- Use `unittest.mock.patch` context managers and purely symbolic paths
  rather than function-scoped pytest fixtures — Hypothesis re-runs the test
  body many times per fixture instance.
- The `.hypothesis/` example database is gitignored; promote any shrunk
  failure to a named unit test rather than relying on the database.

## Quality gates

Run before every commit, in this order:

```bash
make check-fmt
make typecheck
make lint
make test
```

For Markdown changes, also run `make markdownlint` and `make nixie` (nixie
validates its own documentation's Mermaid diagrams).
