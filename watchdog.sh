#!/bin/bash
# Smart watchdog — delegates to mt5_health.py for proper diagnostics

export DISPLAY=:99
FRAMEWORK_DIR="/home/tradingbeast/fundingpips/framework"

# Ensure Xvfb is running
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :99 -screen 0 1920x1080x24 &>/dev/null &
    sleep 2
fi

# Run the Python diagnostic
cd "$FRAMEWORK_DIR" || exit 1
python3 mt5_health.py 2>&1
