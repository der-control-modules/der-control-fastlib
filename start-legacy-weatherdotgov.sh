#!/bin/bash

# Start script for AEMS Legacy WeatherDotGov Agent
# This demonstrates how to start the VOLTTRON WeatherDotGov agent using the launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOLTTRON_HOME="$SCRIPT_DIR/.volttron_home"

echo "============================================================"
echo "AEMS Legacy Agent Launcher - WeatherDotGov Agent"
echo "============================================================"
echo "VOLTTRON_HOME: $VOLTTRON_HOME"
echo ""

# Start WeatherDotGov agent with auto-detection
"$SCRIPT_DIR/.venv/bin/python" -u start-legacy.py \
    --agent-dir /home/volttron/volttron/services/core/WeatherDotGov \
    --config /home/volttron/volttron/services/core/WeatherDotGov/config \
    --address ws://localhost:8000 \
    --volttron-home "$VOLTTRON_HOME" \
    --identity "platform.weather" \
    --log-file "$SCRIPT_DIR/log_platform.weather.log" \
    --debug
