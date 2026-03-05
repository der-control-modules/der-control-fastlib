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
lint: ## Check code with ruff linter
	$(RUFF) check src/ tests/

.PHONY: fix
fix: ## Fix code issues automatically with ruff
	$(RUFF) check --fix src/ tests/

.PHONY: lint-fix
lint-fix: fix format ## Run linter fixes and formatting (like pre-commit)

.PHONY: check
check: lint format-check test ## Run all checks (linting, formatting, tests)

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

# Docker
.PHONY: docker-build
docker-build: ## Build Docker image
	./docker-helper.sh build

.PHONY: docker-run
docker-run: ## Run Docker container
	./docker-helper.sh run

.PHONY: docker-stop
docker-stop: ## Stop Docker container
	./docker-helper.sh stop

.PHONY: docker-logs
docker-logs: ## View Docker container logs
	./docker-helper.sh logs

.PHONY: docker-shell
docker-shell: ## Open shell in Docker container
	./docker-helper.sh shell

.PHONY: docker-test
docker-test: ## Run tests in Docker container
	./docker-helper.sh test

.PHONY: docker-clean
docker-clean: ## Clean Docker container and volumes
	./docker-helper.sh clean

.PHONY: compose-up
compose-up: ## Start services with docker-compose
	docker-compose up -d

.PHONY: compose-down
compose-down: ## Stop services with docker-compose
	docker-compose down

.PHONY: compose-logs
compose-logs: ## View docker-compose logs
	docker-compose logs -f

# Help
.PHONY: help
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
