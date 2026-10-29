#!/bin/bash
# Cron wrapper: Silo Rule Engine — runs every 15 minutes

export DISPLAY=:99
WINE_PY="/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Local/Programs/Python/Python311/python.exe"
FRAMEWORK_DIR="/home/tradingbeast/fundingpips/framework"
LOG="/home/tradingbeast/fundingpips/logs/silo_engine.log"

cd "$FRAMEWORK_DIR" || exit 1

# Ensure Xvfb is running
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :99 -screen 0 1920x1080x24 &>/dev/null &
    sleep 2
fi

# Run silo rule engine
wine "$WINE_PY" -c "
import sys
sys.path.insert(0, '$FRAMEWORK_DIR')
from core.silo_rule_engine import main
main()
" 2>&1

echo "[$(date)] Silo rule engine completed" >> "$LOG"
