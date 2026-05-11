#!/usr/bin/env python3
"""
Stock Analyst
Reads raw_data/stock_YYYY-MM-DD.json → LLM → email

Usage:
  python3 stock_analyst.py        # full run
  python3 stock_analyst.py --test # skip LLM, send [TEST] email
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
from lib.watchlist import get_ticker_meta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _build_thesis_section(tickers: list[str]) -> list[str]:
    """Inject thesis + key risks for each ticker being analyzed."""
    entries = [get_ticker_meta(t) for t in tickers]
    entries = [e for e in entries if e]
    if not entries:
        return []
    lines = ["=" * 60,
             "SECTION 0 — INVESTMENT THESIS & RISK REGISTER (from watchlist.yaml)",
             "=" * 60,
             "Evaluate today's news against each stock's thesis.",
             "Flag explicitly: SUPPORTS ✅ / CHALLENGES ⚠️ / NEUTRAL ➡️",
             "Flag if any news materialises a listed key risk.",
             ""]
    for meta in entries:
        tier   = "core" if not meta.get("upgrade_condition") else "watchlist"
        header = f"{meta['ticker']} ({meta['name']}) — {tier}"
        if tier == "watchlist":
            header += f" | upgrade if: {meta.get('upgrade_condition', '')}"
        lines.append(header)
        lines.append(f"  Thesis: {meta['thesis'].strip()}")
        for r in meta.get("key_risks", []):
            lines.append(f"    Risk: {r}")
        lines.append("")
    return lines


def build_user_message(data: dict, prev_reports: dict[str, str | None],
                       macro_regime: str | None) -> str:
    today       = datetime.now().strftime("%A, %B %d, %Y")
    tickers     = data.get("tickers", [])
    tickers_str = ", ".join(tickers)
    lines = [f"Today is {today}. Stocks under analysis: {tickers_str}\n"]
    lines += _build_thesis_section(tickers)

    lines += ["=" * 60,
              "SECTION A — STOCK DATA (yfinance: price, fundamentals, technicals)",
              "=" * 60, data.get("stock_data_md", "N/A"), ""]

    fundamentals = data.get("fundamentals", {})
    if fundamentals:
        lines += ["=" * 60,
                  "SECTION A2 — RAW FUNDAMENTALS PER TICKER (structured numbers)",
                  "=" * 60]
        for ticker, f in fundamentals.items():
            if not f:
                continue
            eps_hist = f.pop("eps_history", [])
            lines.append(f"\n{ticker}:")
            for k, v in f.items():
                if v is not None:
                    lines.append(f"  {k}: {v}")
            if eps_hist:
                lines.append(f"  eps_history (last 4Q):")
                for q in eps_hist:
                    lines.append(f"    {q}")
            f["eps_history"] = eps_hist  # restore
        lines.append("")

    lines += ["=" * 60, "SECTION B — UPCOMING EARNINGS (Finnhub)", "=" * 60,
              data.get("earnings_md", "N/A"), ""]

    lines += ["=" * 60, "SECTION C — NEWS & MARKET CONTEXT (past 7 days)", "=" * 60]
    for i, item in enumerate(data.get("news_items", []), 1):
        ticker_tag = f" [{item.get('_ticker', '')}]" if item.get("_ticker") else ""
        lines.append(f"\n--- Article {i}{ticker_tag} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    for ticker, prev in prev_reports.items():
        if prev:
            lines += ["", "=" * 60,
                      f"SECTION D — PREVIOUS ANALYSIS BASELINE: {ticker}",
                      "=" * 60, prev]

    if macro_regime:
        lines += ["", "=" * 60,
                  "SECTION E — MACRO REGIME CONTEXT (from macro_analyst, use as backdrop)",
                  "=" * 60, macro_regime]

    lines.append(
        "\nUsing all sections above, produce the full stock analysis report "
        "covering every ticker listed."
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
    parser = argparse.ArgumentParser(description="Stock Analyst")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Stock Analyst %s%s (engine: %s) ===",
             today_str, " (TEST)" if args.test else "", LLM_ENGINE)

    data = load_raw("stock")
    if not data:
        log.error("No stock raw data for today. Run: python3 data_fetcher/fetch_stock.py --ticker ...")
        return

    tickers      = data.get("tickers", [])
    tickers_label = " · ".join(tickers)

    prev_reports: dict[str, str | None] = {}
    if not args.test:
        for ticker in tickers:
            prev_reports[ticker] = load_previous_report(f"stock_{ticker.lower()}")
    else:
        prev_reports = {t: None for t in tickers}

    macro_regime = None if args.test else load_previous_report("macro")

    if args.test:
        english = (
            f"[TEST MODE] {LLM_ENGINE} inference skipped.\n\n"
            f"Tickers: {tickers_label}\n"
            f"{len(data.get('news_items', []))} news items in raw data."
        )
        chinese = f"[测试模式] 已跳过 {LLM_ENGINE} 推理。分析标的：{tickers_label}"
    else:
        prompt   = PromptLoader.load("stock", "analysis")
        user_msg = build_user_message(data, prev_reports, macro_regime)
        response = run_llm(prompt, user_msg, label=f"stock_{tickers_label}")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)
        for ticker in tickers:
            save_report(f"stock_{ticker.lower()}", english)

    news_items = data.get("news_items", [])
    title_text = f"个股分析报告 — {tickers_label}"
    subject    = f"{subject_prefix}【宏观AI实验室】{title_text} — {today_str}"
    html       = render_html(chinese, news_items, ACTIVE_MODEL,
                             title_emoji="🔍", title_text=title_text, style="stock")
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
