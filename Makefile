.PHONY: help all clean test build lint fmt check-fmt markdownlint nixie

RUFF ?= uv run ruff
TY ?= uv run ty
PYTEST ?= uv run pytest
BUILD_JOBS ?=
MDLINT ?= markdownlint
NIXIE ?= nixie

all: check-fmt lint test ## Default target runs preflight checks

clean: ## Remove build artifacts
	rm -rf .venv dist/ *.egg-info

build: ## install deps and build bytecode
	uv venv
	uv sync --group dev

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
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(MDLINT)

nixie: ## Validate Mermaid diagrams
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(NIXIE)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
