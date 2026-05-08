#!/usr/bin/env python3
"""
Macro AI Lab — Individual Stock Analysis Report

Usage:
  python3 stock_analyst.py --ticker AAPL                # single stock, full run
  python3 stock_analyst.py --ticker AAPL MSFT NVDA      # multi-stock report
  python3 stock_analyst.py --ticker AAPL --test         # fast test: skip LLM, send [TEST] email
"""

import argparse
import logging
from datetime import date, datetime, timedelta

import yfinance as yf

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.search import serper_search, fetch_article_text, last_nhours_tbs
from lib.sources import is_trusted_source
from lib.finnhub_client import fetch_earnings_calendar, format_earnings_calendar
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.report_store import save_report, load_previous_report
from lib.prompt_loader import PromptLoader
from lib.stock_data import fetch_stock_data, fetch_yfinance_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Search query builder ──────────────────────────────────────────────────────

def build_search_queries(ticker: str) -> list[str]:
    """Generate news search queries for a single stock ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
        company = info.get("longName", ticker)
        sector  = info.get("sector", "")
    except Exception:
        company = ticker
        sector  = ""

    queries = [
        f"{ticker} {company} earnings revenue results guidance",
        f"{ticker} analyst upgrade downgrade price target rating",
        f"{ticker} {company} news latest week",
        f"{ticker} {company} product launch partnership acquisition risk",
    ]
    if sector:
        queries.append(f"{sector} sector outlook trends this week")
    return queries


# ── News gathering ────────────────────────────────────────────────────────────

def gather_news(tickers: list[str], test_mode: bool = False) -> list[dict]:
    """Collect news from yfinance + Serper for all requested tickers."""
    max_full = 0 if test_mode else 5
    tbs      = last_nhours_tbs(168) if not test_mode else last_nhours_tbs(48)  # 7 days

    seen_urls: set[str] = set()
    results:   list[dict] = []
    full_fetched = 0
    dropped = 0

    # yfinance news (fast, ticker-specific, no API cost)
    if not test_mode:
        yf_raw = fetch_yfinance_news(tickers)
        for item in yf_raw:
            if is_trusted_source(item.get("source", "")):
                url = item.get("link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
            else:
                dropped += 1

    # Serper search — one set of queries per ticker
    query_tickers = tickers[:1] if test_mode else tickers
    for ticker in query_tickers:
        queries = build_search_queries(ticker)
        if test_mode:
            queries = queries[:1]

        for query in queries:
            log.info("Searching [%s]: %s", ticker, query)
            try:
                items = serper_search(query, num=8, tbs=tbs)
            except Exception as exc:
                log.error("Search failed for '%s': %s", query, exc)
                continue

            for item in items:
                url = item.get("link", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if not is_trusted_source(item.get("source", "")):
                    dropped += 1
                    log.debug("Dropped (untrusted): [%s] %s",
                              item.get("source", ""), item.get("title", ""))
                    continue

                item["_ticker"] = ticker  # tag article to ticker

                if full_fetched < max_full and url:
                    log.info("  Fetching full article: %s", url)
                    item["full_text"] = fetch_article_text(url)
                    if item["full_text"]:
                        full_fetched += 1

                results.append(item)

    if dropped:
        log.info("Source filter dropped %d items", dropped)
    log.info("Collected %d news items (%d with full text)", len(results), full_fetched)
    return results


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_user_message(
    tickers:      list[str],
    stock_data:   str,
    earnings_md:  str,
    news_items:   list[dict],
    prev_reports: dict[str, str | None],
    macro_regime: str | None,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    tickers_str = ", ".join(tickers)
    lines = [
        f"Today is {today}. Stocks under analysis: {tickers_str}\n",
        "=" * 60,
        "SECTION A — STOCK DATA (yfinance: price, fundamentals, technicals)",
        "=" * 60,
        stock_data,
        "",
        "=" * 60,
        "SECTION B — UPCOMING EARNINGS (Finnhub)",
        "=" * 60,
        earnings_md,
        "",
        "=" * 60,
        "SECTION C — NEWS & MARKET CONTEXT (past 7 days)",
        "=" * 60,
    ]

    for i, item in enumerate(news_items, 1):
        ticker_tag = f" [{item.get('_ticker', '')}]" if item.get("_ticker") else ""
        lines.append(f"\n--- Article {i}{ticker_tag} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    # Previous reports per ticker for continuity
    for ticker, prev in prev_reports.items():
        if prev:
            lines += [
                "",
                "=" * 60,
                f"SECTION D — PREVIOUS ANALYSIS BASELINE: {ticker}",
                "=" * 60,
                prev,
            ]

    if macro_regime:
        lines += [
            "",
            "=" * 60,
            "SECTION E — MACRO REGIME CONTEXT (from macro_analyst, use as backdrop)",
            "=" * 60,
            macro_regime,
        ]

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Macro AI Lab — Stock Analysis Report")
    parser.add_argument(
        "--ticker", nargs="+", required=True,
        metavar="TICKER",
        help="One or more stock tickers (e.g. AAPL MSFT NVDA)",
    )
    parser.add_argument("--test", action="store_true",
                        help="Fast test: 1 query, skip LLM, send [TEST] email")
    args = parser.parse_args()

    tickers        = [t.upper() for t in args.ticker]
    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""
    tickers_label  = " · ".join(tickers)

    log.info("=== Macro AI Lab — Stock Report | %s | %s%s (engine: %s) ===",
             tickers_label, today_str, " (TEST MODE)" if args.test else "", LLM_ENGINE)

    # Section A — stock price, fundamentals, technicals
    log.info("Fetching stock data for: %s", tickers_label)
    stock_data = "\n\n".join(fetch_stock_data(t) for t in tickers)

    # Section B — upcoming earnings for these specific tickers (next 2 weeks)
    today      = date.today()
    week_end   = today + timedelta(days=14)
    if args.test:
        earnings_events = []
    else:
        from lib.finnhub_client import fetch_earnings_calendar as _fetch_cal
        # Fetch broadly then filter to requested tickers
        all_events = _fetch_cal(today, week_end)
        earnings_events = [e for e in all_events if e.get("symbol") in tickers]
        # If none found, keep the broad list for context
        if not earnings_events:
            earnings_events = all_events
    earnings_md = format_earnings_calendar(earnings_events)

    # Section C — news
    news_items = gather_news(tickers, test_mode=args.test)
    if not news_items:
        log.warning("No news collected. Aborting.")
        return

    # Previous reports per ticker (day-over-day continuity)
    prev_reports: dict[str, str | None] = {}
    if not args.test:
        for ticker in tickers:
            prev_reports[ticker] = load_previous_report(f"stock_{ticker.lower()}")
    else:
        prev_reports = {t: None for t in tickers}

    # Macro regime context
    macro_regime = None if args.test else load_previous_report("macro")

    if args.test:
        english = (
            f"[TEST MODE] {LLM_ENGINE} inference skipped.\n\n"
            f"Tickers: {tickers_label}\n\n"
            f"Stock data fetched successfully:\n{stock_data[:500]}..."
        )
        chinese = (
            f"[测试模式] 已跳过 {LLM_ENGINE} 推理。\n\n"
            f"分析标的：{tickers_label}\n\n"
            f"股票数据已成功获取。"
        )
        log.info("Test mode — skipping LLM inference")
    else:
        prompt   = PromptLoader.load("stock", "analysis")
        user_msg = build_user_message(
            tickers, stock_data, earnings_md, news_items, prev_reports, macro_regime
        )
        response = run_llm(prompt, user_msg, label=f"stock_{tickers_label}")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)

        # Save one report file per ticker (English only, for next-day baseline)
        for ticker in tickers:
            save_report(f"stock_{ticker.lower()}", english)

    # Render & send
    title_text = f"个股分析报告 — {tickers_label}"
    subject    = f"{subject_prefix}【宏观AI实验室】{title_text} — {today_str}"
    html       = render_html(chinese, news_items, ACTIVE_MODEL,
                              title_emoji="🔍", title_text=title_text, style="stock")
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)
        log.info("Report printed above; check SMTP credentials in .env")


if __name__ == "__main__":
    main()
