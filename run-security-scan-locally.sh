#!/bin/bash
# Simulate security-scan workflow locally
# Based on .github/workflows/ci.yml

set -e  # Exit on error

echo "Running security scan locally..."
echo ""

# Use virtual environment if available, otherwise use system python3
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
fi

echo "Installing dependencies..."
python3 -m pip install --upgrade pip
pip install -e .[dev]
echo "Dependencies installed"
echo ""

# Check if we're on main branch
BRANCH=$(git branch --show-current)

if [ "$BRANCH" = "main" ]; then
    echo "Running security scans (STRICT MODE - main branch)..."
    make security-strict
else
    echo "Running security scans (standard mode)..."
    make security
fi

echo "Security scan passed"
echo ""

if [ -f "bandit-report.json" ]; then
    echo "Security reports generated:"
    echo "  - bandit-report.json"
fi

echo "Security scan completed!"
