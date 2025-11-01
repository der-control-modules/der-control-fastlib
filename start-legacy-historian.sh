#!/bin/bash

# Test script for AEMS Legacy Agent Launcher
# This demonstrates how to start a VOLTTRON agent (SQLHistorian) using the launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - Test Script"
echo "Testing with SQLHistorian"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""

# Test 1: Auto-detection (recommended)
echo "Test 1: Auto-detection mode"
echo "  The script will automatically find the agent module and class"
echo "  by searching for vip_main() calls in the agent directory"
echo ""
echo "Command:"
echo "  start-legacy.py --agent-dir /home/volttron/volttron/services/core/SQLHistorian \\"
echo "    --config config.sqlite \\"
echo "    --address ws://localhost:8000 \\"
echo "    --identity platform.historian"
echo ""

"$SCRIPT_DIR/.venv/bin/python" -u start-legacy.py \
    --agent-dir /home/volttron/volttron/services/core/SQLHistorian \
    --config config.sqlite \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "platform.historian" \
    --log-file "$SCRIPT_DIR/log_platform.historian.log"

# Uncomment below to test manual specification
# echo ""
# echo "============================================================"
# echo "Test 2: Manual specification mode"
# echo "  You can explicitly specify the module and class name"
# echo "  if auto-detection doesn't work or you want more control"
# echo ""
# echo "Command:"
# echo "  start-legacy.py --agent-dir /home/volttron/volttron/services/core/SQLHistorian \\"
# echo "    --module sqlhistorian.historian \\"
# echo "    --class SQLHistorian \\"
# echo "    --config config.sqlite \\"
# echo "    --identity platform.historian"
# echo ""
#
# "$SCRIPT_DIR/.venv/bin/python" -u start-legacy.py \
#     --agent-dir /home/volttron/volttron/services/core/SQLHistorian \
#     --module sqlhistorian.historian \
#     --class SQLHistorian \
#     --config config.sqlite \
#     --address ws://localhost:8000 \
#     --volttron-home "$VOLTTRON_HOME" \
#     --identity "platform.historian"
