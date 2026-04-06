"""Finnhub API client — earnings calendar, general news.

Economic calendar (/calendar/economic) requires a paid plan — not used.
Gracefully degrades to empty results when FINNHUB_API_KEY is not set.
"""

import logging
from datetime import date, datetime

import requests

from .config import FINNHUB_API_KEY

log = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"


def _get(path: str, params: dict) -> dict | list:
    if not FINNHUB_API_KEY:
        return {}
    params["token"] = FINNHUB_API_KEY
    resp = requests.get(f"{_BASE}{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Earnings calendar ─────────────────────────────────────────────────────────

# Major companies whose earnings are macro-relevant (tech mega-cap, big banks,
# energy majors). LLM is instructed to score these 5–6; this list avoids flooding
# the prompt with hundreds of small-cap reports.
_MAJOR_TICKERS: set[str] = {
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "META", "AMZN", "TSLA",
    # Big banks / financials
    "JPM", "BAC", "GS", "MS", "C", "WFC", "BLK",
    # Energy majors
    "XOM", "CVX", "COP",
    # Industrials / macro bellwethers
    "CAT", "BA", "MMM", "UNP",
    # Consumer
    "WMT", "HD", "COST", "NKE",
}


def fetch_earnings_calendar(from_date: date, to_date: date) -> list[dict]:
    """Return major-company earnings for the given date range.

    Each item: {symbol, date, hour, epsEstimate, revenueEstimate}
    Only tickers in _MAJOR_TICKERS are returned.
    """
    if not FINNHUB_API_KEY:
        log.warning("FINNHUB_API_KEY not set — skipping earnings calendar")
        return []
    try:
        data = _get("/calendar/earnings", {
            "from": from_date.isoformat(),
            "to":   to_date.isoformat(),
        })
        all_events = data.get("earningsCalendar", []) if isinstance(data, dict) else []
        events = [e for e in all_events if e.get("symbol") in _MAJOR_TICKERS]
        log.info("Finnhub earnings: %d major events this week (%d total)",
                 len(events), len(all_events))
        return events
    except Exception as exc:
        log.warning("Finnhub earnings calendar failed: %s", exc)
        return []


def format_earnings_calendar(events: list[dict]) -> str:
    """Format major earnings as a markdown table for the LLM prompt."""
    if not events:
        return "_No major earnings this week._"

    events = sorted(events, key=lambda e: (e.get("date", ""), e.get("symbol", "")))

    lines = [
        "| Date | Symbol | Time | EPS Estimate | Revenue Estimate |",
        "|:-----|:-------|:-----|-------------:|-----------------:|",
    ]
    for e in events:
        hour = {"bmo": "Pre-market", "amc": "After-close", "dmh": "During hours"}.get(
            e.get("hour", ""), e.get("hour", "—")
        )
        eps = f"{e['epsEstimate']:.2f}" if e.get("epsEstimate") is not None else "—"
        rev = f"${e['revenueEstimate']/1e9:.1f}B" if e.get("revenueEstimate") else "—"
        lines.append(
            f"| {e.get('date', '')} "
            f"| **{e.get('symbol', '')}** "
            f"| {hour} "
            f"| {eps} "
            f"| {rev} |"
        )
    return "\n".join(lines)


# ── General news ──────────────────────────────────────────────────────────────

def fetch_general_news(category: str = "general", last_hours: int = 24) -> list[dict]:
    """Return normalised Finnhub news items published within the last *last_hours* hours.

    Each item: {title, source, link, date, snippet, sentiment}
    """
    if not FINNHUB_API_KEY:
        log.warning("FINNHUB_API_KEY not set — skipping Finnhub news")
        return []
    try:
        raw = _get("/news", {"category": category})
        items = raw if isinstance(raw, list) else []

        cutoff = datetime.utcnow().timestamp() - last_hours * 3600
        items = [n for n in items if (n.get("datetime") or 0) >= cutoff]

        log.info("Finnhub news: %d items in last %dh (category=%s)", len(items), last_hours, category)
        return [_normalise_news(n) for n in items]
    except Exception as exc:
        log.warning("Finnhub news failed: %s", exc)
        return []


def _normalise_news(item: dict) -> dict:
    ts = item.get("datetime", 0)
    try:
        pub_date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
    except (OSError, OverflowError, ValueError):
        pub_date = ""
    return {
        "title":     item.get("headline", ""),
        "source":    item.get("source", ""),
        "link":      item.get("url", ""),
        "date":      pub_date,
        "snippet":   item.get("summary", ""),
        "sentiment": item.get("sentiment", ""),
    }
