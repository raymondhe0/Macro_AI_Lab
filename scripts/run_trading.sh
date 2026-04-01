#!/bin/bash
# Wrapper called by launchd — routes logs to dated files

DATE=$(date +%Y-%m-%d)
LOG_DIR="/Users/ray/Documents/Macro_AI_Lab/logs"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
SCRIPT="/Users/ray/Documents/Macro_AI_Lab/scripts/trading_analyst.py"

# Monday = weekday 1 → run weekly report; all other days → intraday
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" -eq 1 ]; then
    MODE="weekly"
else
    MODE="intraday"
fi

caffeinate -i \
    "$PYTHON" -u "$SCRIPT" --mode "$MODE" \
    >> "${LOG_DIR}/trading_report_${DATE}.log" \
    2>> "${LOG_DIR}/trading_report_error_${DATE}.log"
