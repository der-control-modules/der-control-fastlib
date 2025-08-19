# Makefile for AEMS project

# Variables
PYTHON := .venv/bin/python
PIP := .venv/bin/pip
BLACK := .venv/bin/black
ISORT := .venv/bin/isort
FLAKE8 := .venv/bin/flake8
PYLINT := .venv/bin/pylint
PYTEST := .venv/bin/pytest
PRECOMMIT := .venv/bin/pre-commit

# Formatting and linting
.PHONY: format
format: ## Format code with Black and isort
	$(BLACK) --line-length=120 src/ tests/
	$(ISORT) --profile black --line-length=120 src/ tests/

.PHONY: format-check
format-check: ## Check if code is formatted correctly
	$(BLACK) --line-length=120 --check src/ tests/
	$(ISORT) --profile black --line-length=120 --check-only src/ tests/

.PHONY: lint
lint: ## Run linting with flake8 and pylint
	$(FLAKE8) --max-line-length=120 src/ tests/
	$(PYLINT) --max-line-length=120 src/

.PHONY: lint-fix
lint-fix: format lint ## Format code and then run linting

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
