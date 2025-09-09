# Makefile for AEMS project

# Variables
PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PYTEST := .venv/bin/pytest
PRECOMMIT := .venv/bin/pre-commit
RUFF := .venv/bin/ruff

# Formatting and linting
.PHONY: format
format: ## Format code with ruff
	$(RUFF) format src/ tests/

.PHONY: format-check
format-check: ## Check if code is formatted correctly
	$(RUFF) format --check src/ tests/

.PHONY: lint
lint: ## Run linting with ruff
	$(RUFF) check src/ tests/

.PHONY: lint-fix
lint-fix: fix format ## Fix issues and format code with ruff

.PHONY: fix
fix: ## Fix code issues automatically with ruff
	$(RUFF) check --fix src/ tests/

# Testing
.PHONY: test
test: ## Run tests
	$(PYTEST) tests/

.PHONY: test-cov
test-cov: ## Run tests with coverage
	$(PYTEST) --cov=src --cov-report=html --cov-report=term tests/

# Pre-commit
.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks
	$(PRECOMMIT) install

.PHONY: pre-commit-run
pre-commit-run: ## Run pre-commit on all files
	$(PRECOMMIT) run --all-files

# Development setup
.PHONY: dev-install
dev-install: ## Install development dependencies
	$(PIP) install -e ".[dev]"
	$(PRECOMMIT) install

# Clean
.PHONY: clean
clean: ## Clean up temporary files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/
	rm -rf dist/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/

# Help
.PHONY: help
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
