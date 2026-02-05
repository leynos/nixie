.PHONY: help all clean test build lint fmt check-fmt markdownlint nixie benchmark

RUFF ?= uv run ruff
TY ?= uv run ty
PYTEST ?= uv run pytest
BUILD_JOBS ?=
MDLINT ?= npx --yes markdownlint-cli
NIXIE ?= uv run nixie
HYPERFINE ?= hyperfine
BENCH_DOCS ?= tests/fixtures/benchmark_sample

all: check-fmt lint test ## Default target runs preflight checks

clean: ## Remove build artifacts
	rm -rf .venv dist/ *.egg-info

build: ## install deps and build bytecode
	@if [ -x .venv/bin/python ]; then \
		echo "venv present; skipping uv setup"; \
	else \
		uv venv && uv sync --group dev; \
	fi

test: build ## Run tests
	$(PYTEST) -v

lint: ## Run lint
	$(RUFF) check

typecheck: build ## Run type checking
	$(TY) check

fmt: ## Format code
	$(RUFF) format

check-fmt: ## Verify formatting
	$(RUFF) format --check

markdownlint: ## Lint Markdown files
	git ls-files '*.md' \
		':!.rules/**' \
		':!tests/fixtures/benchmark_docs/**' \
		':!tests/fixtures/benchmark_sample/**' \
	| tr '\n' '\0' \
	| xargs -0 --no-run-if-empty -- $(MDLINT)

nixie: ## Validate Mermaid diagrams
	git ls-files '*.md' \
		':!.rules/**' \
		':!tests/fixtures/benchmark_docs/**' \
		':!tests/fixtures/benchmark_sample/**' \
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
