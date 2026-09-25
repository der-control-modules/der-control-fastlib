#!/bin/bash
# Simulate CI workflow locally
# Based on .github/workflows/ci.yml

set -e  # Exit on error

echo "Running CI checks locally..."
echo ""

# Use virtual environment if available, otherwise use system python3
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
fi

# Test job
echo "Installing dependencies..."
python3 -m pip install --upgrade pip
pip install -e .[dev]
echo "Dependencies installed"
echo ""

echo "Running format check..."
ruff check src/
echo "Format check passed"
echo ""

echo "Running linting..."
make lint
echo "Linting passed"
echo ""

echo "Running tests..."
make test
echo "Tests passed"
echo ""

echo "Testing build..."
make build
echo "Build test passed"
echo ""

echo "All CI checks passed!"
