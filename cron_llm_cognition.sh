#!/bin/bash
# Cron wrapper: LLM Cognition Pass — every 15 minutes
# Runs on LINUX system Python (not wine) — wine Python cannot spawn subprocesses

cd /home/tradingbeast/fundingpips/framework || exit 1

TRIGGER="${1:-scheduled}"

python3 cognition_linux.py "$TRIGGER" 2>&1

echo "[$(date)] LLM cognition completed (trigger: $TRIGGER)" >> /home/tradingbeast/fundingpips/logs/llm_cognition.log
