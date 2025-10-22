# Test script for AEMS Legacy Agent Launcher
# This demonstrates how to start a VOLTTRON agent (PlatformDriverAgent) using the launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - Test Script"
echo "Testing with PlatformDriverAgent"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""

# Test 1: Auto-detection (recommended)
echo "Test 1: Auto-detection mode"
echo "  The script will automatically find the agent module and class"
echo "  by searching for vip_main() calls in the agent directory"
echo ""
echo "Command:"
echo "  start-legacy.py --agent-dir /home/volttron/volttron/services/core/PlatformDriverAgent \\"
echo "    --address ws://localhost:8000 \\"
echo "    --identity platform.driver"
echo ""

"$SCRIPT_DIR/.venv/bin/python" start-legacy.py \
    --agent-dir /home/volttron/volttron/services/core/PlatformDriverAgent \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "platform.driver"
