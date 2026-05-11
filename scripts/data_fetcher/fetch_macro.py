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
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

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

    prices_md  = fetch_macro_prices()
    rates_md   = fetch_rates_data()

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
        "earnings_md": earnings_md,
        "news_items":  news_items,
    })
    log.info("Done.")


if __name__ == "__main__":
    main()
