#!/bin/bash
# Cron wrapper: Per-Silo Report — runs daily at 23:00 UTC+3
# Generates per-silo FundingPips report and sends to Telegram

export DISPLAY=:0
export XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.P0RDX3
export WINEPREFIX=/home/tradingbeast/.wine
WINE_PY="/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Local/Programs/Python/Python311/python.exe"
FRAMEWORK_DIR="/home/tradingbeast/fundingpips/framework"

cd "$FRAMEWORK_DIR" || exit 1

# Capture report output — a silent NameError here means no report is sent.
{
  echo "===== REPORT $(date) ====="
  wine "$WINE_PY" -c "
import sys
sys.path.insert(0, '$FRAMEWORK_DIR')
from core.silo_report import main
main()
" 2>&1
  echo "===== END REPORT ====="
} | tee -a /home/tradingbeast/fundingpips/logs/silo_report.log
