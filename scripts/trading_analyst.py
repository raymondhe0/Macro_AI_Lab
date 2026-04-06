#!/usr/bin/env python3
"""
Macro AI Lab — NQ & GC Trading Strategy Report

Usage:
  python3 trading_analyst.py                        # intraday, full run
  python3 trading_analyst.py --mode weekly          # weekly report
  python3 trading_analyst.py --test                 # fast test, [TEST] email
  python3 trading_analyst.py --mode weekly --test   # weekly test
"""

import argparse
import logging
from datetime import date, datetime

import yfinance as yf

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.search import serper_search, fetch_article_text, last_24h_tbs
from lib.sources import is_trusted_source
from lib.finnhub_client import fetch_economic_calendar, fetch_general_news, format_economic_calendar
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.previous_log import load_previous_log
from lib.prompt_loader import PromptLoader

# ── Queries ───────────────────────────────────────────────────────────────────

INTRADAY_QUERIES = [
    "Nasdaq futures NQ premarket outlook today",
    "Gold futures GC price outlook today",
    "site:cnbc.com Fed speakers inflation economic data today",
    "US real yields TIPS gold relationship today",
    "VIX volatility index level today",
    "economic calendar key events today US market",
]

WEEKLY_EXTRA_QUERIES = [
    "CFTC COT report gold futures net positioning latest",
    "CFTC COT report Nasdaq large speculator positioning latest",
    "site:investing.com economic calendar this week major events",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Technical levels via yfinance ─────────────────────────────────────────────

def _compute_pivots(high: float, low: float, close: float) -> dict:
    pp = (high + low + close) / 3
    return {"PP": pp, "R1": 2*pp - low, "R2": pp + (high - low),
            "S1": 2*pp - high, "S2": pp - (high - low)}


def _fetch_levels(ticker: str, label: str) -> str:
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
        if hist.empty or len(hist) < 6:
            return f"{label} ({ticker}): insufficient data from yfinance."

        prior_week = hist.iloc[-6:-1]
        pw_high, pw_low = prior_week["High"].max(), prior_week["Low"].min()
        pw_close = hist.iloc[-2]["Close"]
        current  = hist["Close"].iloc[-1]
        ma20  = hist["Close"].rolling(20).mean().iloc[-1]
        ma50  = hist["Close"].rolling(50).mean().iloc[-1]
        ma200 = hist["Close"].rolling(200).mean().iloc[-1]
        pv    = _compute_pivots(pw_high, pw_low, pw_close)

        return "\n".join([
            f"\n### {label} ({ticker}) — Technical Levels (auto-fetched via yfinance)\n",
            "| Level            | Price      |",
            "|:-----------------|:-----------|",
            f"| Current Price    | {current:>10.2f} |",
            f"| Prior Week High  | {pw_high:>10.2f} |",
            f"| Prior Week Low   | {pw_low:>10.2f} |",
            f"| Prior Week Close | {pw_close:>10.2f} |",
            f"| Pivot Point (PP) | {pv['PP']:>10.2f} |",
            f"| Resistance 1 (R1)| {pv['R1']:>10.2f} |",
            f"| Resistance 2 (R2)| {pv['R2']:>10.2f} |",
            f"| Support 1 (S1)   | {pv['S1']:>10.2f} |",
            f"| Support 2 (S2)   | {pv['S2']:>10.2f} |",
            f"| 20-Day MA        | {ma20:>10.2f} |",
            f"| 50-Day MA        | {ma50:>10.2f} |",
            f"| 200-Day MA       | {ma200:>10.2f} |",
        ])
    except Exception as exc:
        log.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return f"{label} ({ticker}): could not fetch technical levels — {exc}"


def get_all_technical_levels() -> str:
    log.info("Fetching technical levels via yfinance…")
    return _fetch_levels("NQ=F", "Nasdaq 100 Futures (NQ)") + "\n\n" + \
           _fetch_levels("GC=F", "Gold Futures (GC)")


# ── yfinance news ─────────────────────────────────────────────────────────────

def fetch_yfinance_news(tickers: tuple = ("NQ=F", "GC=F"), max_per_ticker: int = 8) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()

    for ticker in tickers:
        try:
            news = yf.Ticker(ticker).news or []
            log.info("yfinance news for %s: %d items", ticker, len(news))
            for item in news[:max_per_ticker]:
                content = item.get("content", item)
                url = (content.get("canonicalUrl", {}).get("url", "")
                       or content.get("clickThroughUrl", {}).get("url", "")
                       or item.get("link", ""))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                pub_date = content.get("pubDate", "") or item.get("providerPublishTime", "")
                if isinstance(pub_date, (int, float)):
                    pub_date = datetime.utcfromtimestamp(pub_date).strftime("%Y-%m-%d")

                provider = (content.get("provider", {}).get("displayName", "")
                            or item.get("publisher", ""))
                summary  = (content.get("summary", "") or content.get("description", "")
                            or item.get("summary", ""))
                results.append({
                    "title":      content.get("title", item.get("title", "")),
                    "source":     provider,
                    "link":       url,
                    "date":       pub_date,
                    "snippet":    summary,
                    "_yf_ticker": ticker,
                })
        except Exception as exc:
            log.warning("yfinance news fetch failed for %s: %s", ticker, exc)

    log.info("yfinance news total: %d unique items", len(results))
    return results


# ── News gathering ────────────────────────────────────────────────────────────

def gather_news(mode: str, test_mode: bool) -> list[dict]:
    queries  = INTRADAY_QUERIES[:1] if test_mode else INTRADAY_QUERIES
    max_full = 0 if test_mode else 4
    tbs      = None if test_mode else last_24h_tbs()

    if mode == "weekly" and not test_mode:
        queries = queries + WEEKLY_EXTRA_QUERIES

    yf_raw = [] if test_mode else fetch_yfinance_news()
    yf_news = [n for n in yf_raw if is_trusted_source(n.get("source", ""))]
    if len(yf_raw) - len(yf_news):
        log.info("Source filter dropped %d yfinance items", len(yf_raw) - len(yf_news))

    seen_urls: set[str] = set(item["link"] for item in yf_news)
    results:   list[dict] = list(yf_news)
    full_fetched, dropped_serper = 0, 0

    for item in ([] if test_mode else fetch_general_news()):
        url = item.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            results.append(item)

    for query in queries:
        log.info("Searching: %s", query)
        try:
            items = serper_search(query, tbs=tbs)
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            continue

        for item in items:
            url    = item.get("link", "")
            source = item.get("source", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not is_trusted_source(source):
                dropped_serper += 1
                log.debug("Dropped (untrusted): [%s] %s", source, item.get("title", ""))
                continue

            if full_fetched < max_full and url:
                log.info("  Fetching full article: %s", url)
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1
            results.append(item)

    if dropped_serper:
        log.info("Source filter dropped %d Serper items", dropped_serper)
    log.info("Collected %d trusted news items (%d with full text)", len(results), full_fetched)
    return results


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_user_message(
    news_items: list[dict],
    tech_levels: str,
    calendar_md: str,
    mode: str,
    prev_log: str | None,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    is_weekly = mode == "weekly"
    lines = [
        f"Today is {today}. Mode: {'WEEKLY strategy' if is_weekly else 'INTRADAY strategy'}.\n",
        "=" * 60, "SECTION A — TECHNICAL LEVELS (yfinance)", "=" * 60,
        tech_levels, "",
        "=" * 60, "SECTION B — ECONOMIC CALENDAR (Finnhub)", "=" * 60,
        calendar_md, "",
        "=" * 60, "SECTION C — NEWS & MARKET CONTEXT", "=" * 60,
    ]

    for i, item in enumerate(news_items, 1):
        lines.append(f"\n--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    if prev_log:
        lines += ["", "=" * 60, "SECTION D — PREVIOUS DAY'S ANALYSIS BASELINE",
                  "=" * 60, prev_log]

    lines.append(
        f"\nUsing the technical levels from Section A, economic calendar from Section B, "
        f"and news from Section C, produce the {'weekly' if is_weekly else 'intraday'} strategy report."
    )
    return "\n".join(lines)


def _parse_bilingual(response: str) -> tuple[str, str]:
    marker = "[BEGIN_CHINESE_TRANSLATION]"
    if marker in response:
        eng, chn = response.split(marker, 1)
        return eng.strip(), chn.strip()
    return response.strip(), response.strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Macro AI Lab — Trading Report")
    parser.add_argument("--mode", choices=["intraday", "weekly"], default="intraday")
    parser.add_argument("--test", action="store_true",
                        help="Fast test: 1 query, skip LLM, send [TEST] email")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    if args.mode == "weekly":
        report_type = "NQ & GC 周度策略报告"
        subject     = f"{subject_prefix}【宏观AI实验室】NQ & GC 周度策略 — Week of {today_str}"
        prompt_name = "weekly_nq_gc"
    else:
        report_type = "NQ & GC 日内策略报告"
        subject     = f"{subject_prefix}【宏观AI实验室】NQ & GC 日内策略 — {today_str}"
        prompt_name = "intraday_nq_gc"

    log.info("=== Macro AI Lab — %s %s%s (engine: %s) ===",
             prompt_name, today_str, " (TEST MODE)" if args.test else "", LLM_ENGINE)

    tech_levels = get_all_technical_levels()

    today       = date.today()
    cal_events  = [] if args.test else fetch_economic_calendar(today, today)
    calendar_md = format_economic_calendar(cal_events)

    news_items = gather_news(mode=args.mode, test_mode=args.test)
    if not news_items:
        log.warning("No news collected. Aborting.")
        return

    prev_log = None if args.test else load_previous_log(f"trading_{args.mode}")

    if args.test:
        english = (f"[TEST MODE — {prompt_name}] {LLM_ENGINE} inference skipped.\n\n"
                   f"Technical levels fetched successfully:\n{tech_levels}")
        chinese = (f"[测试模式 — {prompt_name}] 已跳过 {LLM_ENGINE} 推理。\n\n"
                   f"技术位已成功获取：\n{tech_levels}")
        log.info("Test mode — skipping LLM inference")
    else:
        prompt   = PromptLoader.load("trading", prompt_name)
        user_msg = build_user_message(news_items, tech_levels, calendar_md, args.mode, prev_log)
        response = run_llm(prompt, user_msg, label=prompt_name)
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)

    html = render_html(chinese, news_items, ACTIVE_MODEL,
                       title_emoji="📈", title_text=report_type, style="trading")
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
