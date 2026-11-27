.PHONY: help all clean prepare test build lint fmt check-fmt markdownlint spelling nixie benchmark \
	interrogate

# Every uv call goes through the vendored helper, which cleans the environment,
# uses the global uv cache, runs offline first and goes online at most once. See
# https://github.com/leynos/shared-actions/tree/main/uv_gate for the contract.
# `scripts/uv_gate.py` is the shared-actions helper, vendored byte for byte, so
# update it by copying the canonical file, never by editing it here.
UV_GATE ?= python3 scripts/uv_gate.py
RUFF ?= $(UV_GATE) run --group dev -- ruff
TY ?= $(UV_GATE) run --group dev -- ty
PYTEST ?= $(UV_GATE) run --group dev -- pytest
BUILD_JOBS ?=
MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || printf '%s' "$$HOME/.bun/bin/markdownlint-cli2")
# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version at the
# install-mdtablefix step.
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
# Directories whose Markdown is verbatim third-party or benchmark input. Every
# Markdown target skips them: the git pathspecs below, and mdtablefix, which
# has no exclude option, so its selection is filtered by prefix instead.
MARKDOWN_EXCLUDE_DIRS = .rules tests/fixtures/benchmark_docs tests/fixtures/benchmark_sample
MARKDOWN_EXCLUDES = $(foreach dir,$(MARKDOWN_EXCLUDE_DIRS),':!$(dir)/**')
empty :=
space := $(empty) $(empty)
MARKDOWN_EXCLUDE_RE = ^($(subst $(space),|,$(subst .,\.,$(MARKDOWN_EXCLUDE_DIRS))))/
NIXIE ?= $(UV_GATE) run --group dev -- nixie
HYPERFINE ?= hyperfine
BENCH_DOCS ?= tests/fixtures/benchmark_sample
# Interrogate measures docstring presence over every Python file the repository
# owns: the package, the local pathspec shim, the scripts and every test suite.
# Ruff owns docstring style.
INTERROGATE_VERSION ?= 1.7.0
INTERROGATE_TARGETS ?= nixie pathspec scripts tests
# Level 2 prints a MISSED row per undocumented definition, which the
# `interrogate` target fails on: the rounded percentage alone passes a lone gap.
INTERROGATE_FLAGS ?= -vv --fail-under 100
INTERROGATE = $(UV_GATE) tool --python 3.14 \
	--from 'interrogate==$(INTERROGATE_VERSION)' -- interrogate
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.4
TYPOS_CONFIG_BUILDER = uv tool run --python 3.14 --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder

all: check-fmt lint test ## Default target runs preflight checks
	+$(MAKE) spelling

clean: ## Remove build artifacts
	rm -rf .venv build dist/ *.egg-info
	rm -f .typos-oxendict-base.json .typos-oxendict-base.toml

prepare: ## Sync the locked environment, offline first
	$(UV_GATE) prepare --group dev

build: prepare ## install deps and build bytecode

test: build ## Run tests
	$(PYTEST) -v

lint: prepare interrogate ## Run lint, including Python docstring coverage
	$(RUFF) check

# Interrogate rounds its percentage to one decimal, so `--fail-under 100` alone
# passes a lone gap in a large repository; fail on any MISSED row as well.
interrogate: ## Enforce complete Python docstring coverage
	@report="$$($(INTERROGATE) $(INTERROGATE_FLAGS) $(INTERROGATE_TARGETS))"; \
	  status=$$?; \
	  printf '%s\n' "$$report"; \
	  test "$$status" -eq 0 || exit "$$status"; \
	  if printf '%s\n' "$$report" | grep -q '| *MISSED |'; then \
	    printf '%s\n' 'interrogate: undocumented definitions remain; see the MISSED rows above' >&2; \
	    exit 1; \
	  fi

typecheck: build ## Run type checking
	$(TY) check

fmt: prepare ## Format code
	$(RUFF) format
	@if [ -n "$$(git ls-files --unmerged)" ]; then \
		echo "make fmt: unresolved merge conflicts; resolve them first" >&2; \
		exit 1; \
	fi
	files=$$($(MDTABLEFIX) --list-files $(MDTABLEFIX_SELECT)) && \
	printf '%s\n' "$$files" | grep -v -E '$(MARKDOWN_EXCLUDE_RE)' | tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(MDTABLEFIX) --in-place $(MDTABLEFIX_RULES)
	$(MDLINT) --fix "**/*.md"

check-fmt: prepare ## Verify formatting
	$(RUFF) format --check
	files=$$($(MDTABLEFIX) --list-files $(MDTABLEFIX_SELECT)) && \
	printf '%s\n' "$$files" | grep -v -E '$(MARKDOWN_EXCLUDE_RE)' | tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(MDTABLEFIX) --check $(MDTABLEFIX_RULES)

markdownlint: ## Lint Markdown files
	git ls-files '*.md' \
		$(MARKDOWN_EXCLUDES) \
	| tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(MDLINT)
	+$(MAKE) spelling

# The gate reads tracked files only, so stage new Markdown files before running
# this target; untracked files are not checked.
spelling: ## Enforce en-GB-oxendict spelling in prose
	$(TYPOS_CONFIG_BUILDER) gate --repository .

nixie: prepare ## Validate Mermaid diagrams
	git ls-files '*.md' \
		$(MARKDOWN_EXCLUDES) \
	| tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(NIXIE)

benchmark: build ## Benchmark serial vs bounded-concurrent validation
	@if ! command -v $(HYPERFINE) >/dev/null 2>&1; then \
		echo "hyperfine is required for benchmarks"; \
		exit 1; \
	fi
	$(HYPERFINE) \
		'$(NIXIE) $(BENCH_DOCS) --max-concurrency 1' \
		'$(NIXIE) $(BENCH_DOCS)'

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
