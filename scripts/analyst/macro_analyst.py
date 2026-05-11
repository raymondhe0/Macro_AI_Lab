#!/usr/bin/env python3
"""
Macro Analyst
Reads raw_data/macro_YYYY-MM-DD.json → LLM → email

Usage:
  python3 macro_analyst.py        # full run
  python3 macro_analyst.py --test # skip LLM, send [TEST] email
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.report_store import save_report, load_previous_report
from lib.prompt_loader import PromptLoader
from lib.raw_store import load_raw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def build_user_message(data: dict, prev_report: str | None) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"Today is {today}. Below are the latest macro inputs:\n"]

    lines += ["=" * 60, "SECTION A — LIVE MARKET PRICES (Finnhub ETF proxies)", "=" * 60,
              data.get("prices_md", "N/A"), ""]
    lines += ["=" * 60, "SECTION A2 — YIELD CURVE & FED FUNDS RATE (FRED)", "=" * 60,
              data.get("rates_md", "N/A"), ""]
    lines += ["=" * 60, "SECTION B — MAJOR EARNINGS THIS WEEK (Finnhub)", "=" * 60,
              data.get("earnings_md", "N/A"), ""]

    lines += ["=" * 60, "SECTION C — NEWS & MARKET CONTEXT", "=" * 60]
    for i, item in enumerate(data.get("news_items", []), 1):
        lines.append(f"\n--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    if prev_report:
        lines += ["", "=" * 60,
                  "SECTION D — PREVIOUS DAY'S ANALYSIS BASELINE (continuity context)",
                  "=" * 60, prev_report]

    lines.append(
        "\nApply Noise Filtering first, then produce the Playbook analysis "
        "for every event that scores 7 or higher."
    )
    return "\n".join(lines)


def _parse_bilingual(response: str) -> tuple[str, str]:
    marker = "[BEGIN_CHINESE_TRANSLATION]"
    if marker not in response:
        log.warning("Bilingual marker not found — email will render English only")
        return response.strip(), response.strip()
    eng, chn = response.split(marker, 1)
    return eng.strip(), chn.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro Analyst")
    parser.add_argument("--test", action="store_true",
                        help="Skip LLM, send [TEST] email")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Macro Analyst %s%s (engine: %s) ===",
             today_str, " (TEST)" if args.test else "", LLM_ENGINE)

    data = load_raw("macro")
    if not data:
        log.error("No macro raw data for today. Run: python3 data_fetcher/fetch_macro.py")
        return

    prev_report = None if args.test else load_previous_report("macro")

    if args.test:
        english = f"[TEST MODE] {LLM_ENGINE} inference skipped. {len(data.get('news_items', []))} news items in raw data."
        chinese = f"[测试模式] 已跳过 {LLM_ENGINE} 推理。"
    else:
        prompt   = PromptLoader.load("macro", "analysis")
        user_msg = build_user_message(data, prev_report)
        response = run_llm(prompt, user_msg, label="macro_analysis")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)
        save_report("macro", english)

    news_items = data.get("news_items", [])
    html       = render_html(chinese, news_items, ACTIVE_MODEL,
                             title_emoji="📊", title_text="每日宏观策略报告", style="macro")
    subject    = f"{subject_prefix}【宏观AI实验室】每日策略报告 — {today_str}"
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
