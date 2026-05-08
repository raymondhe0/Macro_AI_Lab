#!/usr/bin/env python3
"""
One-off script: translate an existing English trading report to Chinese and resend the email.

Usage:
  python3 resend_trading_report.py                  # reads reports/trading_intraday_<latest>.md
  python3 resend_trading_report.py --file path.md   # use a specific file
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from lib.config import ACTIVE_MODEL
from lib.llm import run_llm
from lib.email_report import render_html, send_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

TRANSLATE_SYSTEM = """You are a professional financial translator. Translate the following English futures trading strategy report into Simplified Chinese (简体中文).

Rules:
- Preserve ALL markdown formatting exactly (###, **, |table|, ---, &nbsp;, etc.)
- Keep all instrument names, price levels, and numbers unchanged (e.g., NQ, GC, 25000, R1, S2, MA)
- Keep all emoji and special symbols unchanged (📈, 📉, ⚠️, 🟢, 🔴)
- Use standard Chinese futures/trading terminology:
    纳斯达克100期货, 黄金期货, 多头/空头, 止损, 目标位, 支撑位, 阻力位,
    枢轴点, 风险回报比, 日内交易, 美联储, 美元指数, 实际收益率,
    多头论据, 空头论据, 压力测试, 布林带, 平均真实波幅,
    前收盘价, 前日高点, 前日低点, 枢轴点
- Do not add explanations or commentary — pure translation only.
"""


def find_latest_report(mode: str) -> Path | None:
    reports_dir = Path(__file__).parent.parent / "reports"
    candidates = sorted(reports_dir.glob(f"trading_{mode}_*.md"), reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Retranslate and resend a trading report")
    parser.add_argument("--file", help="Path to the English report .md file")
    parser.add_argument("--mode", choices=["intraday", "weekly"], default="intraday")
    args = parser.parse_args()

    if args.file:
        report_path = Path(args.file)
    else:
        report_path = find_latest_report(args.mode)
        if not report_path:
            log.error("No saved trading_%s report found in reports/. Run trading_analyst.py first.", args.mode)
            sys.exit(1)

    log.info("Reading report: %s", report_path)
    english = report_path.read_text(encoding="utf-8").strip()

    if not english:
        log.error("Report file is empty.")
        sys.exit(1)

    log.info("Translating to Chinese via LLM…")
    chinese = run_llm(TRANSLATE_SYSTEM, english, label="translation-only")
    log.info("Translation complete (%d chars)", len(chinese))

    today_str = datetime.now().strftime("%Y-%m-%d")
    if args.mode == "weekly":
        report_type = "NQ & GC 周度策略报告"
        subject = f"【宏观AI实验室】NQ & GC 周度策略 (重发) — Week of {today_str}"
    else:
        report_type = "NQ & GC 日内策略报告"
        subject = f"【宏观AI实验室】NQ & GC 日内策略 (重发) — {today_str}"

    html = render_html(chinese, [], ACTIVE_MODEL,
                       title_emoji="📈", title_text=report_type, style="trading")

    send_email(subject, html, english)
    log.info("Email sent successfully.")


if __name__ == "__main__":
    main()
