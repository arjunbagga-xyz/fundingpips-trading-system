#!/bin/bash
# Cron wrapper: Psychology Audit — daily at 23:30 UTC+3
# Runs on LINUX system Python (not wine)

cd /home/tradingbeast/fundingpips/framework || exit 1

python3 psych_audit_linux.py 2>&1

echo "[$(date)] Psych audit completed" >> /home/tradingbeast/fundingpips/logs/psych_audit.log
