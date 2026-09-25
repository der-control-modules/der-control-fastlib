#!/bin/bash
# Test script for running legacy VOLTTRON agents

set -e

echo "================================================"
echo "Testing Legacy VOLTTRON Agent Support"
echo "================================================"
echo

# Check if server is running
if ! curl -s http://localhost:8000/health/ > /dev/null 2>&1; then
    echo "❌ AEMS server is not running on localhost:8000"
    echo
    echo "Please start the server in another terminal:"
    echo "  source .venv/bin/activate"
    echo "  aems-server --host 0.0.0.0 --port 8000"
    echo
    exit 1
fi

echo "✓ AEMS server is running"
echo

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found"
    echo "Run ./setup-dev.sh first"
    exit 1
fi

source .venv/bin/activate
echo "✓ Virtual environment activated"
echo

# Test the legacy agent launcher
echo "================================================"
echo "Running ListenerAgent via start-legacy.py"
echo "================================================"
echo
echo "Command:"
echo "./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \\"
echo "    --config config \\"
echo "    --identity test_listener \\"
echo "    --address ws://localhost:8000"
echo
echo "Press Ctrl+C to stop the agent"
echo "================================================"
echo

exec ./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \
    --config config \
    --identity test_listener \
    --address ws://localhost:8000 \
    --debug
