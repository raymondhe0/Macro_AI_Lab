#!/usr/bin/env python3
"""
Tech Data Fetcher
Fetches AI/tech sector news + prices → raw_data/tech_YYYY-MM-DD.json

Usage:
  python3 fetch_tech.py           # full fetch
  python3 fetch_tech.py --test    # fast: 1 query, skip source filter
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf
from stockstats import wrap as ss_wrap

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.search import serper_search, fetch_article_text, last_24h_tbs, last_nhours_tbs
from lib.sources import is_trusted_source
from lib.stock_data import fetch_yfinance_news
from lib.raw_store import save_raw
from lib.watchlist import ticker_universe_for_fetcher

SEARCH_QUERIES = [
    "NVIDIA GPU H100 Blackwell supply demand data center revenue outlook",
    "AI capital expenditure Microsoft Meta Amazon Google cloud infrastructure spending",
    "semiconductor chip TSMC AMD Intel AI accelerator supply chain latest",
    "Google Gemini TPU AI model release performance benchmark competitive",
    "Anthropic Claude model funding valuation enterprise adoption latest",
    "OpenAI GPT model partnership Microsoft revenue commercialization",
    "xAI Grok Elon Musk talent departure SpaceX merger AI lab news",
    "AI regulation EU AI Act US executive order antitrust Big Tech",
    "AI enterprise adoption productivity SaaS software revenue impact latest",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def gather_news(test_mode: bool = False) -> list[dict]:
    queries      = SEARCH_QUERIES[:1] if test_mode else SEARCH_QUERIES
    max_full     = 0 if test_mode else 8
    num          = 5 if test_mode else 10
    tbs          = last_nhours_tbs(72) if test_mode else last_nhours_tbs(48)
    seen_urls: set[str] = set()
    results:   list[dict] = []
    full_fetched = 0

    if not test_mode:
        tickers = list(ticker_universe_for_fetcher().keys())
        for item in fetch_yfinance_news(tickers):
            if is_trusted_source(item.get("source", ""), tech=True):
                url = item.get("link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
        log.info("yfinance news: %d items after source filter", len(results))

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
            if not test_mode and not is_trusted_source(item.get("source", ""), tech=True):
                continue
            if full_fetched < max_full and url:
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1
            results.append(item)

    log.info("Collected %d tech news items (%d with full text)", len(results), full_fetched)
    return results


def _fetch_fundamentals(tkr: yf.Ticker) -> dict:
    """Extract valuation + earnings quality from yfinance. Returns {} on failure."""
    try:
        info = tkr.info or {}
    except Exception:
        return {}

    def _f(key: float | None, decimals: int = 2) -> float | None:
        v = info.get(key)
        return round(float(v), decimals) if v is not None else None

    # EPS beat/miss — last 4 quarters
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
                    "actual":       round(float(actual), 2)   if actual   is not None else None,
                    "estimate":     round(float(estimate), 2) if estimate is not None else None,
                    "surprise":     round(float(surprise), 2) if surprise is not None else None,
                    "surprise_pct": round(float(pct) * 100, 1) if pct is not None else None,
                })
    except Exception as exc:
        log.debug("EPS history unavailable: %s", exc)

    return {
        "pe_trailing":       _f("trailingPE", 1),
        "pe_forward":        _f("forwardPE", 1),
        "ev_revenue":        _f("enterpriseToRevenue", 1),
        "ev_ebitda":         _f("enterpriseToEbitda", 1),
        "price_to_sales":    _f("priceToSalesTrailing12Months", 1),
        "peg_ratio":         _f("pegRatio", 2),
        "revenue_growth_yoy":_f("revenueGrowth", 3),   # e.g. 0.782 = 78.2%
        "earnings_growth_yoy":_f("earningsGrowth", 3),
        "gross_margin":      _f("grossMargins", 3),     # e.g. 0.744 = 74.4%
        "operating_margin":  _f("operatingMargins", 3),
        "net_margin":        _f("profitMargins", 3),
        "return_on_equity":  _f("returnOnEquity", 3),
        "free_cashflow_ttm": info.get("freeCashflow"),  # raw dollars
        "market_cap":        info.get("marketCap"),
        "trailing_eps":      _f("trailingEps", 2),
        "forward_eps":       _f("forwardEps", 2),
        "analyst_target":    _f("targetMeanPrice", 2),
        "analyst_count":     info.get("numberOfAnalystOpinions"),
        "recommendation":    info.get("recommendationKey"),  # "buy", "hold", etc.
        "short_interest_pct":_f("shortPercentOfFloat", 3),
        "eps_history":       eps_history,
    }


def fetch_tech_stock_data() -> list[dict]:
    """Fetch price, technicals, and fundamentals for every stock in TECH_STOCK_UNIVERSE.

    All values are raw numbers (floats/ints), not pre-formatted strings,
    so the LLM can reason about actual magnitudes directly.
    """
    records = []
    for ticker, description in ticker_universe_for_fetcher().items():
        try:
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="2y", interval="1d")
            if hist.empty or len(hist) < 52:
                log.warning("Insufficient history for %s", ticker)
                continue

            price    = round(float(hist["Close"].iloc[-1]), 2)
            prev     = round(float(hist["Close"].iloc[-2]), 2)
            chg_1d   = round((price / prev - 1) * 100, 2)

            # 52-week and 2-year price range
            hist_1y  = hist.iloc[-252:] if len(hist) >= 252 else hist
            high_52w = round(float(hist_1y["High"].max()), 2)
            low_52w  = round(float(hist_1y["Low"].min()), 2)
            high_2y  = round(float(hist["High"].max()), 2)
            low_2y   = round(float(hist["Low"].min()), 2)

            # Percentile within range (0 = at low, 1 = at high)
            pct_52w = round((price - low_52w) / (high_52w - low_52w), 3) if high_52w != low_52w else None
            pct_2y  = round((price - low_2y)  / (high_2y  - low_2y),  3) if high_2y  != low_2y  else None

            ma_20  = round(float(hist["Close"].rolling(20).mean().iloc[-1]), 2)
            ma_50  = round(float(hist["Close"].rolling(50).mean().iloc[-1]), 2)
            ma_200 = round(float(hist["Close"].rolling(200).mean().iloc[-1]), 2)

            hist_r = hist.reset_index()
            hist_r.columns = [c.lower() for c in hist_r.columns]
            ss  = ss_wrap(hist_r)
            rsi = round(float(ss["rsi"].iloc[-1]), 1)
            atr = round(float(ss["atr"].iloc[-1]), 2)

            fundamentals = _fetch_fundamentals(tkr)

            records.append({
                "ticker":      ticker,
                "description": description,
                # ── Price & technicals ──
                "price":             price,
                "prev_close":        prev,
                "change_1d_pct":     chg_1d,
                "high_52w":          high_52w,
                "low_52w":           low_52w,
                "pct_off_52w_high":  round((price / high_52w - 1) * 100, 2),
                "price_pct_52w_range": pct_52w,   # 0.91 = in top 9% of 52w range
                "price_pct_2y_range":  pct_2y,
                "ma_20":   ma_20,
                "ma_50":   ma_50,
                "ma_200":  ma_200,
                "vs_ma50_pct":  round((price / ma_50 - 1) * 100, 2),
                "vs_ma200_pct": round((price / ma_200 - 1) * 100, 2),
                "rsi_14":  rsi,
                "atr_14":  atr,
                "above_ma50":  price > ma_50,
                "above_ma200": price > ma_200,
                # ── Fundamentals ──
                **fundamentals,
            })
            log.info("  %s: $%.2f  P/E fwd=%.1f  RevGrowth=%.0f%%  RSI=%.0f  52w-pct=%.0f%%",
                     ticker, price,
                     fundamentals.get("pe_forward") or 0,
                     (fundamentals.get("revenue_growth_yoy") or 0) * 100,
                     rsi, (pct_52w or 0) * 100)
        except Exception as exc:
            log.warning("Failed to fetch %s: %s", ticker, exc)

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Tech Data Fetcher")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    log.info("=== Tech Data Fetcher %s%s ===",
             datetime.now().strftime("%Y-%m-%d"), " (TEST)" if args.test else "")

    log.info("Fetching tech stock prices & technicals...")
    tech_stocks = fetch_tech_stock_data()

    news_items = gather_news(test_mode=args.test)

    save_raw("tech", {
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "tech_stocks": tech_stocks,   # structured JSON — raw numbers, not markdown
        "news_items":  news_items,
    })
    log.info("Done. %d stocks, %d news items.", len(tech_stocks), len(news_items))


if __name__ == "__main__":
    main()
