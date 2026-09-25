#!/bin/bash

# Start script for VOLTTRON EmailerAgent
# Sends email notifications from AEMS agents via SMTP

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

IDENTITY="platform.emailer"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - EmailerAgent"
echo "Testing with VOLTTRON EmailerAgent"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""
echo "Command:"
echo "  start-legacy.py --agent-dir /home/volttron/volttron/services/ops/EmailerAgent \\"
echo "    --config configs/emailer/config \\"
echo "    --address ws://localhost:8000 \\"
echo "    --identity $IDENTITY"
echo ""
echo "NOTE: Copy configs/emailer/config.example to configs/emailer/config"
echo "      and fill in your SMTP credentials before running."
echo ""

"$SCRIPT_DIR/.venv/bin/python" -u start-legacy.py \
    --agent-dir /home/volttron/volttron/services/ops/EmailerAgent \
    --config "$SCRIPT_DIR/configs/emailer/config" \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "$IDENTITY" \
    --log-file "$SCRIPT_DIR/log_$IDENTITY.log"
