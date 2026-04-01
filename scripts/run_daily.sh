#!/bin/bash
# Wrapper called by launchd — routes logs to dated files

DATE=$(date +%Y-%m-%d)
LOG_DIR="/Users/ray/Documents/Macro_AI_Lab/logs"

# caffeinate -i keeps the Mac awake until the python script finishes
caffeinate -i \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -u \
    /Users/ray/Documents/Macro_AI_Lab/scripts/macro_analyst.py \
    >> "${LOG_DIR}/daily_report_${DATE}.log" \
    2>> "${LOG_DIR}/daily_report_error_${DATE}.log"
