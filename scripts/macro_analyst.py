#!/usr/bin/env python3
"""
Macro AI Lab — Daily Macro Strategy Report

Usage:
  python3 macro_analyst.py           # full run (engine set by LLM_ENGINE in .env)
  python3 macro_analyst.py --test    # fast test: 1 query, no LLM call, sends [TEST] email
"""

import argparse
import logging
from datetime import date, datetime, timedelta

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.search import serper_search, fetch_article_text, last_24h_tbs, last_nhours_tbs
from lib.sources import is_trusted_source
from lib.finnhub_client import fetch_earnings_calendar, format_earnings_calendar, fetch_general_news
from lib.market_data import fetch_macro_prices
from lib.rates_data import fetch_rates_data
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.report_store import save_report, load_previous_report
from lib.prompt_loader import PromptLoader

# ── Queries ───────────────────────────────────────────────────────────────────

SEARCH_QUERIES = [
    # Critical (9–10): Fed + major data prints
    "Federal Reserve FOMC (Federal Open Market Committee) interest rate decision inflation outlook",
    "CPI (Consumer Price Index) inflation NFP (Non-Farm Payrolls) nonfarm payrolls economic data latest",

    # High (7–8): PMI, trade/tariffs, OPEC+, other CBs
    "PMI (Purchasing Managers Index) manufacturing services miss beat",
    "tariff trade policy US China fiscal spending announcement",
    "OPEC (Organization of the Petroleum Exporting Countries) oil supply cut energy prices geopolitical risk",
    "ECB (European Central Bank) BOJ (Bank of Japan) central bank rate decision hawkish dovish surprise",

    # Inter-market signals — two focused queries
    "VIX (Volatility Index) credit spreads high yield bond market risk sentiment",
    "DXY (US Dollar Index) 10-year Treasury yield gold price WTI crude oil",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── News gathering ────────────────────────────────────────────────────────────

def gather_news(test_mode: bool = False) -> list[dict]:
    queries  = SEARCH_QUERIES[:1] if test_mode else SEARCH_QUERIES
    max_full = 0 if test_mode else 6
    num      = 5 if test_mode else 10
    tbs      = last_nhours_tbs(48) if test_mode else last_24h_tbs()

    seen_urls: set[str] = set()
    results:   list[dict] = []
    full_fetched = 0
    dropped = 0

    for item in ([] if test_mode else fetch_general_news()):
        url = item.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            results.append(item)

    for query in queries:
        log.info("Searching: %s", query)
        try:
            items = serper_search(query, num=num, tbs=tbs)
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            continue

        for item in items:
            url = item.get("link", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not is_trusted_source(item.get("source", ""), macro=True):
                dropped += 1
                log.debug("Dropped (untrusted): [%s] %s", item.get("source", ""), item.get("title", ""))
                continue

            if full_fetched < max_full and url:
                log.info("  Fetching full article: %s", url)
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1

            results.append(item)

    if dropped:
        log.info("Source filter dropped %d items (untrusted sources)", dropped)
    log.info("Collected %d news items (%d with full text)", len(results), full_fetched)
    return results


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_user_message(
    news_items:   list[dict],
    prices_md:    str,
    rates_md:     str,
    earnings_md:  str,
    prev_report:  str | None,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"Today is {today}. Below are the latest macro inputs:\n"]

    lines += ["=" * 60, "SECTION A — LIVE MARKET PRICES (Finnhub ETF proxies, real-time)", "=" * 60,
              prices_md, ""]

    lines += ["=" * 60, "SECTION A2 — YIELD CURVE & FED FUNDS RATE (FRED, previous close)", "=" * 60,
              rates_md, ""]

    lines += ["=" * 60, "SECTION B — MAJOR EARNINGS THIS WEEK (Finnhub)", "=" * 60,
              earnings_md, ""]

    lines += ["=" * 60, "SECTION C — NEWS & MARKET CONTEXT", "=" * 60]
    for i, item in enumerate(news_items, 1):
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
                  "SECTION D — PREVIOUS DAY'S ANALYSIS BASELINE (use as continuity context)",
                  "=" * 60, prev_report]

    lines.append(
        "\nApply Noise Filtering first, then produce the Playbook analysis "
        "for every event that scores 7 or higher."
    )
    return "\n".join(lines)


def _parse_bilingual(response: str) -> tuple[str, str]:
    marker = "[BEGIN_CHINESE_TRANSLATION]"
    if marker not in response:
        log.warning("Bilingual marker not found in LLM response — "
                    "email will render English; check if model truncated output")
        return response.strip(), response.strip()
    eng, chn = response.split(marker, 1)
    return eng.strip(), chn.strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Macro AI Lab — Daily Report")
    parser.add_argument("--test", action="store_true",
                        help="Fast test: 1 query, skip LLM, send [TEST] email")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Macro AI Lab — Daily Report %s%s (engine: %s) ===",
             today_str, " (TEST MODE)" if args.test else "", LLM_ENGINE)

    # Section A — real-time ETF prices via Finnhub /quote
    prices_md = fetch_macro_prices()

    # Section A2 — yield curve + Fed Funds rate via FRED (no API key needed)
    rates_md = fetch_rates_data()

    # Section B — major earnings this week (Monday → Sunday)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())   # Monday
    week_end   = week_start + timedelta(days=6)            # Sunday
    earnings_events = [] if args.test else fetch_earnings_calendar(week_start, week_end)
    earnings_md = format_earnings_calendar(earnings_events)

    # Section C — news
    news_items = gather_news(test_mode=args.test)
    if not news_items:
        log.warning("No news collected. Aborting.")
        return

    # Section D — previous day's clean report (from reports/, not logs/)
    prev_report = None if args.test else load_previous_report("macro")

    if args.test:
        english = f"[TEST MODE] {LLM_ENGINE} inference skipped. Pipeline smoke-test only."
        chinese = f"[测试模式] 已跳过 {LLM_ENGINE} 推理。仅验证邮件发送流程。"
        log.info("Test mode — skipping LLM inference")
    else:
        prompt   = PromptLoader.load("macro", "analysis")
        user_msg = build_user_message(news_items, prices_md, rates_md, earnings_md, prev_report)
        response = run_llm(prompt, user_msg, label="analysis+translation")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)

        # Save clean English report to reports/ for tomorrow's baseline
        save_report("macro", english)

    html    = render_html(chinese, news_items, ACTIVE_MODEL,
                          title_emoji="📊", title_text="每日宏观策略报告", style="macro")
    subject = f"{subject_prefix}【宏观AI实验室】每日策略报告 — {today_str}"
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)
        log.info("Report printed above; check SMTP credentials in .env")


if __name__ == "__main__":
    main()
