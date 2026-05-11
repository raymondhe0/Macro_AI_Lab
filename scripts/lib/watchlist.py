"""Load and query the stock watchlist from config/watchlist.yaml."""

from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "watchlist.yaml"


def load_watchlist() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def get_stocks(tier: str = "all") -> list[dict]:
    """Return stock entries for a given tier.

    tier: "core" | "watchlist" | "all"
    """
    wl = load_watchlist()
    if tier == "core":
        return wl.get("core", [])
    if tier == "watchlist":
        return wl.get("watchlist", [])
    return wl.get("core", []) + wl.get("watchlist", [])


def get_tickers(tier: str = "all") -> list[str]:
    return [s["ticker"] for s in get_stocks(tier)]


def get_ticker_meta(ticker: str) -> dict | None:
    """Return the full metadata entry for a ticker, or None if not found."""
    for stock in get_stocks("all"):
        if stock["ticker"].upper() == ticker.upper():
            return stock
    return None


def ticker_universe_for_fetcher() -> dict[str, str]:
    """Return {ticker: label} dict for use in data fetchers.

    Label format: '<name> — <thesis first sentence>'
    """
    result = {}
    for stock in get_stocks("all"):
        thesis_first = stock.get("thesis", "").strip().split("\n")[0].strip()
        result[stock["ticker"]] = f"{stock['name']} — {thesis_first}"
    return result
