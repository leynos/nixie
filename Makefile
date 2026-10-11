.PHONY: help all clean test build lint fmt check-fmt markdownlint spelling nixie benchmark \
	interrogate

RUFF ?= uv run ruff
TY ?= uv run ty
PYTEST ?= uv run pytest
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
NIXIE ?= uv run nixie
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
INTERROGATE = uv tool run --python 3.14 \
	--from 'interrogate==$(INTERROGATE_VERSION)' interrogate
TYPOS_VERSION ?= 1.48.0
TYPOS := uv tool run typos@$(TYPOS_VERSION)

all: check-fmt lint test ## Default target runs preflight checks
	+$(MAKE) spelling

clean: ## Remove build artifacts
	rm -rf .venv build dist/ *.egg-info
	rm -f .typos-oxendict-base.json .typos-oxendict-base.toml

build: ## install deps and build bytecode
	@if [ -x .venv/bin/python ]; then \
		echo "venv present; skipping uv setup"; \
	else \
		uv venv && uv sync --group dev; \
	fi

test: build ## Run tests
	$(PYTEST) -v

lint: interrogate ## Run lint, including Python docstring coverage
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

fmt: ## Format code
	$(RUFF) format
	@if [ -n "$$(git ls-files --unmerged)" ]; then \
		echo "make fmt: unresolved merge conflicts; resolve them first" >&2; \
		exit 1; \
	fi
	files=$$($(MDTABLEFIX) --list-files $(MDTABLEFIX_SELECT)) && \
	printf '%s\n' "$$files" | grep -v -E '$(MARKDOWN_EXCLUDE_RE)' | tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(MDTABLEFIX) --in-place $(MDTABLEFIX_RULES)
	$(MDLINT) --fix "**/*.md"

check-fmt: ## Verify formatting
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

spelling: ## Enforce en-GB-oxendict spelling in maintained Markdown prose
	uv run scripts/generate_typos_config.py
	git ls-files '*.md' \
		$(MARKDOWN_EXCLUDES) \
	| tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(TYPOS) --config typos.toml --force-exclude

nixie: ## Validate Mermaid diagrams
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
		'uv run nixie $(BENCH_DOCS) --max-concurrency 1' \
		'uv run nixie $(BENCH_DOCS)'

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
