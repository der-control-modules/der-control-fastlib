#!/bin/bash

# Start script for AEMS NF (Normal Framework) BACnet Driver
# Uses the AEMS-native driver from the volttron-pnnl-aems repo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

IDENTITY="platform.driver"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - NF Driver"
echo "Testing with Normal Framework BACnet Driver"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""
echo "Command:"
echo "  start-legacy.py --agent-dir /home/volttron/aems-nf/aems-edge/Normal \\"
echo "    --config /home/volttron/aems-nf/aems-edge/Normal/config \\"
echo "    --address ws://localhost:8000 \\"
echo "    --identity $IDENTITY"
echo ""

"$SCRIPT_DIR/.venv/bin/python" -u start-legacy.py \
    --agent-dir /home/volttron/aems-nf/aems-edge/Normal \
    --config /home/volttron/aems-nf/aems-edge/Normal/config \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "$IDENTITY" \
    --log-file "$SCRIPT_DIR/log_$IDENTITY.log"
