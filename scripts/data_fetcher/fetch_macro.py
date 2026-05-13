#!/usr/bin/env python3
"""
Macro Data Fetcher
Fetches macro news, prices, rates, earnings → raw_data/macro_YYYY-MM-DD.json

Usage:
  python3 fetch_macro.py           # full fetch
  python3 fetch_macro.py --test    # fast: 1 query, skip source filter
"""

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.search import serper_search, fetch_article_text, last_24h_tbs, last_nhours_tbs
from lib.sources import is_trusted_source
from lib.finnhub_client import fetch_earnings_calendar, format_earnings_calendar, fetch_general_news
from lib.market_data import fetch_macro_prices
from lib.rates_data import fetch_rates_data
from lib.raw_store import save_raw

SEARCH_QUERIES = [
    "Federal Reserve FOMC interest rate decision inflation outlook",
    "CPI inflation NFP nonfarm payrolls economic data latest",
    "PMI manufacturing services miss beat",
    "tariff trade policy US China fiscal spending announcement",
    "OPEC oil supply cut energy prices geopolitical risk",
    "ECB BOJ central bank rate decision hawkish dovish surprise",
    "VIX credit spreads high yield bond market risk sentiment",
    "DXY 10-year Treasury yield gold price WTI crude oil",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── FinViz market breadth ─────────────────────────────────────────────────────

_FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_SP500_COUNT = 503   # approximate S&P 500 constituent count
_FINVIZ_TIMEOUT = 15

# Thresholds from Lei's notes
_TOP_RISK_PCT   = 85.0   # short-term top when breadth this high
_BOTTOM_PCT     = 15.0   # bottom-watch when breadth this low
_BULL_200D_PCT  = 50.0   # 200-day >50% = bull market environment


def _parse_finviz_count(html: str) -> int | None:
    """Extract total stock count from a FinViz screener HTML response.

    FinViz renders the count in the form '#1 / 223 Total' inside the page.
    Returns the integer count, or None if the pattern is not found.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.find(string=re.compile(r"#\d+\s*/\s*\d+\s+Total"))
    if text is None:
        return None
    m = re.search(r"/\s*(\d+)\s+Total", text)
    return int(m.group(1)) if m else None


def _fetch_finviz_breadth_pct(ma_period: int, index_filter: str = "idx_sp500") -> float | None:
    """Fetch the percentage of index stocks above a given simple moving average.

    Queries FinViz screener for stocks in `index_filter` that are trading
    above their `ma_period`-day SMA. Divides by _SP500_COUNT to get a
    percentage (0–100).

    Args:
        ma_period: SMA period to check (e.g. 20, 50, 200).
        index_filter: FinViz index filter string (default: 'idx_sp500').

    Returns:
        Percentage as a float (e.g. 44.3), or None on network / parse failure.
    """
    url = (f"https://finviz.com/screener.ashx"
           f"?v=111&f={index_filter},ta_sma{ma_period}_pa")
    try:
        resp = requests.get(url, headers=_FINVIZ_HEADERS, timeout=_FINVIZ_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("FinViz request failed (SMA%d): %s", ma_period, exc)
        return None

    count = _parse_finviz_count(resp.text)
    if count is None:
        log.warning("FinViz parse failed (SMA%d): count not found in response", ma_period)
        return None

    return round(count / _SP500_COUNT * 100, 1)


def _breadth_signal(pct: float, horizon: str) -> str:
    """Map a breadth percentage to a Lei-style signal string."""
    if pct >= _TOP_RISK_PCT:
        return f"⚠️ 极端超买 ({pct}%) — 短期顶部风险"
    if pct >= 70:
        return f"偏多 ({pct}%)"
    if pct <= _BOTTOM_PCT:
        return f"⚠️ 极端超卖 ({pct}%) — 底部关注区"
    if pct <= 30:
        return f"偏空 ({pct}%)"
    return f"中性 ({pct}%)"


def fetch_market_breadth() -> str:
    """Fetch S&P 500 market breadth from FinViz screener.

    Scrapes FinViz to count the number of S&P 500 stocks above their
    20/50/200-day SMA, then converts to a percentage.  Falls back to
    a concise 'N/A' row on failure so the rest of the pipeline is not blocked.

    Interpretation thresholds (Lei's notes):
      >85% any horizon → short-term top risk
      <15% any horizon → bottom-watch
      200-day >50%     → bull market environment
    """
    ma_periods = [
        (20,  "S&P 500 % above 20-day MA",  "short-term"),
        (50,  "S&P 500 % above 50-day MA",  "mid-term"),
        (200, "S&P 500 % above 200-day MA", "long-term / bull environment"),
    ]

    rows: list[str] = []
    pct_200: float | None = None

    for ma, label, horizon in ma_periods:
        pct = _fetch_finviz_breadth_pct(ma)
        if pct is None:
            rows.append(f"| {label} | N/A | 获取失败 |")
            continue
        if ma == 200:
            pct_200 = pct
        rows.append(f"| {label} | {pct}% | {_breadth_signal(pct, horizon)} |")

    header = ["| 指标 | 当前值 | 信号 |", "|:-----|-------:|:-----|"]
    result = "\n".join(header + rows)

    if pct_200 is not None:
        env = ("🟢 牛市环境 (>50%)" if pct_200 > _BULL_200D_PCT
               else "🔴 熊市环境 (<50%)")
        result += f"\n**牛熊分界线：** S&P 500 200日宽度 {pct_200}% → {env}"

    return result


def gather_news(test_mode: bool = False) -> list[dict]:
    queries      = SEARCH_QUERIES[:1] if test_mode else SEARCH_QUERIES
    max_full     = 0 if test_mode else 6
    num          = 5 if test_mode else 10
    tbs          = last_nhours_tbs(48) if test_mode else last_24h_tbs()
    seen_urls: set[str] = set()
    results:   list[dict] = []
    full_fetched = 0

    for item in ([] if test_mode else fetch_general_news()):
        if not is_trusted_source(item.get("source", ""), macro=True):
            continue
        url = item.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            results.append(item)

    for query in queries:
        log.info("Searching: %s", query)
        try:
            items = serper_search(query, num=num, tbs=tbs)
        except Exception as exc:
            log.error("Search failed: %s", exc)
            continue
        for item in items:
            url = item.get("link", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if not test_mode and not is_trusted_source(item.get("source", ""), macro=True):
                continue
            if full_fetched < max_full and url:
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1
            results.append(item)

    log.info("Collected %d news items (%d with full text)", len(results), full_fetched)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro Data Fetcher")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    log.info("=== Macro Data Fetcher %s%s ===",
             datetime.now().strftime("%Y-%m-%d"), " (TEST)" if args.test else "")

    prices_md   = fetch_macro_prices()
    rates_md    = fetch_rates_data()
    breadth_md  = fetch_market_breadth()

    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)
    earnings_events = [] if args.test else fetch_earnings_calendar(week_start, week_end)
    earnings_md     = format_earnings_calendar(earnings_events)

    news_items = gather_news(test_mode=args.test)

    save_raw("macro", {
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "prices_md":   prices_md,
        "rates_md":    rates_md,
        "breadth_md":  breadth_md,
        "earnings_md": earnings_md,
        "news_items":  news_items,
    })
    log.info("Done.")


if __name__ == "__main__":
    main()
