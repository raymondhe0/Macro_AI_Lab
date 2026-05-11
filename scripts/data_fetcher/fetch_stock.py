#!/usr/bin/env python3
"""
Stock Data Fetcher
Fetches stock fundamentals, technicals, news → raw_data/stock_YYYY-MM-DD.json

Usage:
  python3 fetch_stock.py --ticker NVDA GOOGL MSFT    # full fetch
  python3 fetch_stock.py --ticker NVDA --test        # fast test
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.search import serper_search, fetch_article_text, last_nhours_tbs
from lib.sources import is_trusted_source
from lib.finnhub_client import fetch_earnings_calendar, format_earnings_calendar
from lib.stock_data import fetch_stock_data, fetch_yfinance_news
from lib.raw_store import save_raw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def build_search_queries(ticker: str) -> list[str]:
    try:
        info    = yf.Ticker(ticker).info or {}
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


def gather_news(tickers: list[str], test_mode: bool = False) -> list[dict]:
    max_full = 0 if test_mode else 5
    tbs      = last_nhours_tbs(168) if not test_mode else last_nhours_tbs(48)
    seen_urls: set[str] = set()
    results:   list[dict] = []
    full_fetched = 0
    dropped = 0

    if not test_mode:
        for item in fetch_yfinance_news(tickers):
            if is_trusted_source(item.get("source", ""), tech=True):
                url = item.get("link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
            else:
                dropped += 1

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
                log.error("Search failed: %s", exc)
                continue
            for item in items:
                url = item.get("link", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                if not test_mode and not is_trusted_source(item.get("source", ""), tech=True):
                    dropped += 1
                    continue
                item["_ticker"] = ticker
                if full_fetched < max_full and url:
                    item["full_text"] = fetch_article_text(url)
                    if item["full_text"]:
                        full_fetched += 1
                results.append(item)

    if dropped:
        log.info("Source filter dropped %d items", dropped)
    log.info("Collected %d news items (%d with full text)", len(results), full_fetched)
    return results


def fetch_stock_fundamentals(ticker: str) -> dict:
    """Fetch valuation, margins, earnings history for a single ticker."""
    try:
        tkr  = yf.Ticker(ticker)
        info = tkr.info or {}
        hist = tkr.history(period="2y", interval="1d")
    except Exception as exc:
        log.warning("fundamentals fetch failed for %s: %s", ticker, exc)
        return {}

    def _f(key, decimals=2):
        v = info.get(key)
        return round(float(v), decimals) if v is not None else None

    # Price range percentiles
    price    = float(hist["Close"].iloc[-1]) if not hist.empty else None
    hist_1y  = hist.iloc[-252:] if len(hist) >= 252 else hist
    high_52w = float(hist_1y["High"].max()) if not hist_1y.empty else None
    low_52w  = float(hist_1y["Low"].min())  if not hist_1y.empty else None
    high_2y  = float(hist["High"].max())    if not hist.empty    else None
    low_2y   = float(hist["Low"].min())     if not hist.empty    else None

    pct_52w = round((price - low_52w) / (high_52w - low_52w), 3) \
              if price and high_52w and low_52w and high_52w != low_52w else None
    pct_2y  = round((price - low_2y)  / (high_2y  - low_2y),  3) \
              if price and high_2y  and low_2y  and high_2y  != low_2y  else None

    # EPS beat/miss last 4 quarters
    eps_history: list[dict] = []
    try:
        eh = tkr.earnings_history
        if eh is not None and not eh.empty:
            for _, row in eh.tail(4).iterrows():
                actual   = row.get("epsActual")
                estimate = row.get("epsEstimate")
                surprise = row.get("epsDifference")
                pct      = row.get("surprisePercent")
                eps_history.append({
                    "quarter":      str(row.name.date()) if hasattr(row.name, "date") else str(row.name),
                    "actual":       round(float(actual), 2)    if actual   is not None else None,
                    "estimate":     round(float(estimate), 2)  if estimate is not None else None,
                    "surprise":     round(float(surprise), 2)  if surprise is not None else None,
                    "surprise_pct": round(float(pct) * 100, 1) if pct      is not None else None,
                })
    except Exception as exc:
        log.debug("EPS history unavailable for %s: %s", ticker, exc)

    return {
        "pe_trailing":        _f("trailingPE", 1),
        "pe_forward":         _f("forwardPE", 1),
        "ev_revenue":         _f("enterpriseToRevenue", 1),
        "ev_ebitda":          _f("enterpriseToEbitda", 1),
        "price_to_sales":     _f("priceToSalesTrailing12Months", 1),
        "peg_ratio":          _f("pegRatio", 2),
        "revenue_growth_yoy": _f("revenueGrowth", 3),
        "earnings_growth_yoy":_f("earningsGrowth", 3),
        "gross_margin":       _f("grossMargins", 3),
        "operating_margin":   _f("operatingMargins", 3),
        "net_margin":         _f("profitMargins", 3),
        "return_on_equity":   _f("returnOnEquity", 3),
        "free_cashflow_ttm":  info.get("freeCashflow"),
        "market_cap":         info.get("marketCap"),
        "trailing_eps":       _f("trailingEps", 2),
        "forward_eps":        _f("forwardEps", 2),
        "analyst_target":     _f("targetMeanPrice", 2),
        "analyst_count":      info.get("numberOfAnalystOpinions"),
        "recommendation":     info.get("recommendationKey"),
        "short_interest_pct": _f("shortPercentOfFloat", 3),
        "price_pct_52w_range": pct_52w,
        "price_pct_2y_range":  pct_2y,
        "eps_history":         eps_history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Data Fetcher")
    parser.add_argument("--ticker", nargs="+", required=True, metavar="TICKER")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    tickers   = [t.upper() for t in args.ticker]
    today_str = datetime.now().strftime("%Y-%m-%d")

    log.info("=== Stock Data Fetcher | %s | %s%s ===",
             " · ".join(tickers), today_str, " (TEST)" if args.test else "")

    stock_data_md = "\n\n".join(fetch_stock_data(t) for t in tickers)
    fundamentals  = {t: fetch_stock_fundamentals(t) for t in tickers}
    log.info("Fundamentals fetched for: %s", list(fundamentals.keys()))

    today    = date.today()
    week_end = today + timedelta(days=14)
    if args.test:
        earnings_events = []
    else:
        all_events      = fetch_earnings_calendar(today, week_end)
        earnings_events = [e for e in all_events if e.get("symbol") in tickers] or all_events
    earnings_md = format_earnings_calendar(earnings_events)

    news_items = gather_news(tickers, test_mode=args.test)

    save_raw("stock", {
        "date":          today_str,
        "tickers":       tickers,
        "stock_data_md": stock_data_md,   # formatted markdown (price + technicals)
        "fundamentals":  fundamentals,    # raw numbers per ticker
        "earnings_md":   earnings_md,
        "news_items":    news_items,
    })
    log.info("Done.")


if __name__ == "__main__":
    main()
