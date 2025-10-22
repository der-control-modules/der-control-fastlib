# Test script for AEMS Legacy Agent Launcher
# This demonstrates how to start a VOLTTRON agent (ListenerAgent) using the launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - Test Script"
echo "Testing with ListenerAgent"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""

# Test 1: Auto-detection (recommended)
echo "Test 1: Auto-detection mode"
echo "  The script will automatically find the agent module and class"
echo "  by searching for vip_main() calls in the agent directory"
echo ""
echo "Command:"
echo "  start-legacy.py --agent-dir /home/volttron/volttron/examples/ListenerAgent \\"
echo "    --address ws://localhost:8000 \\"
echo "    --identity platform.listener"
echo ""

"$SCRIPT_DIR/.venv/bin/python" start-legacy.py \
    --agent-dir /home/volttron/volttron/examples/ListenerAgent \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "platform.listener"
