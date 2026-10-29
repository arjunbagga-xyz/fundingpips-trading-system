#!/bin/bash
# Cron wrapper: Silo Rule Engine — runs every 15 minutes
# Reuses existing MT5 session, retries connection if needed

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

# Ensure MT5 is running (do NOT kill existing session)
if ! pgrep -f 'terminal64.exe' > /dev/null; then
    echo "[$(date)] MT5 not running — starting..." >> "$LOG"
    cd "/home/tradingbeast/.mt5/drive_c/Program Files/MetaTrader 5" || exit 1
    nohup wine terminal64.exe &>/dev/null &
    sleep 45
else
    echo "[$(date)] MT5 already running — reusing session" >> "$LOG"
fi

# Run silo rule engine with connection retry
# Engine stdout/stderr MUST be captured — otherwise reconcile/signal output
# is silently discarded and diagnostics become impossible.
wine "$WINE_PY" -c "
import sys, time
sys.path.insert(0, '$FRAMEWORK_DIR')
from core.silo_rule_engine import main
for attempt in range(3):
    try:
        results = main()
        break
    except Exception as e:
        print(f'Attempt {attempt+1} failed: {e}')
        time.sleep(10)
" 2>&1 | tee -a "$LOG"

echo "[$(date)] Silo rule engine completed" >> "$LOG"
