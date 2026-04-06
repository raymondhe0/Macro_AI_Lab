"""Live spot prices for key macro instruments via Finnhub /quote.

ETF proxies are used since Finnhub covers equities/ETFs, not raw index tickers:
  SPY  → S&P 500 index        QQQ  → Nasdaq 100 index
  GLD  → Gold (XAU/USD)       USO  → WTI Crude Oil
  TLT  → 20-Year Treasury     HYG  → High Yield credit spread proxy
  UUP  → US Dollar (DXY proxy) VIXY → VIX proxy

Gracefully returns a "no data" message if FINNHUB_API_KEY is not set.
"""

import logging
from datetime import datetime, timezone

import requests

from .config import FINNHUB_API_KEY

log = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"

# (symbol, display label)
_INSTRUMENTS: list[tuple[str, str]] = [
    ("SPY",  "S&P 500 (SPY)"),
    ("QQQ",  "Nasdaq 100 (QQQ)"),
    ("VIXY", "VIX proxy (VIXY)"),
    ("TLT",  "20Y Treasury (TLT)"),
    ("HYG",  "HY Credit (HYG)"),
    ("UUP",  "DXY proxy (UUP)"),
    ("GLD",  "Gold (GLD)"),
    ("USO",  "WTI Crude (USO)"),
]

# Interpretation notes injected after the price table so the LLM does not
# misread ETF prices as the underlying instrument level.
_PROXY_NOTES = """\
ETF proxy interpretation notes (IMPORTANT — do NOT use these prices as spot levels):
- GLD price × ~10 ≈ gold spot USD/oz  (e.g., GLD at $300 → gold ~$3,000/oz)
- VIXY is a directional proxy — its share price does NOT equal the VIX index value
- UUP is a directional proxy — its share price does NOT equal the DXY index value
- TLT price moves INVERSELY to 20-year Treasury yields (price ↑ = yields ↓)
- HYG price moves INVERSELY to high-yield credit spreads (price ↓ = spreads wider)
- USO tracks rolling front-month WTI futures and does NOT equal WTI spot price\
"""


def _quote(symbol: str) -> dict | None:
    try:
        resp = requests.get(
            f"{_BASE}/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Finnhub returns c=0 when market is closed and no data exists
        return data if data.get("c") else None
    except Exception as exc:
        log.warning("Finnhub quote failed for %s: %s", symbol, exc)
        return None


def fetch_macro_prices() -> str:
    """Return a formatted markdown table of real-time prices for key macro instruments."""
    if not FINNHUB_API_KEY:
        log.warning("FINNHUB_API_KEY not set — skipping live market prices")
        return "_Live market prices unavailable (FINNHUB_API_KEY not set)._"

    rows = []
    for symbol, label in _INSTRUMENTS:
        q = _quote(symbol)
        if q is None:
            rows.append({"label": label, "symbol": symbol, "error": True})
        else:
            rows.append({
                "label":  label,
                "symbol": symbol,
                "price":  q["c"],
                "change": q["d"],
                "pct":    q["dp"],
                "high":   q["h"],
                "low":    q["l"],
                "prev":   q["pc"],
            })

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"_Prices as of {ts} — ETF proxies, real-time via Finnhub_\n",
        "| Instrument | Price | Chg | Chg% | Day High | Day Low |",
        "|:-----------|------:|----:|-----:|---------:|--------:|",
    ]

    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['label']} | — | — | — | — | — |")
            continue
        arrow = "▲" if r["change"] >= 0 else "▼"
        sign  = "+" if r["change"] >= 0 else ""
        lines.append(
            f"| {r['label']} "
            f"| {r['price']:.2f} "
            f"| {arrow} {abs(r['change']):.2f} "
            f"| {sign}{r['pct']:.2f}% "
            f"| {r['high']:.2f} "
            f"| {r['low']:.2f} |"
        )

    lines += ["", _PROXY_NOTES]
    return "\n".join(lines)
