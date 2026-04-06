"""Treasury yield curve and Fed Funds rate via FRED public API.

No API key required — FRED public CSV endpoint is openly accessible.
Data reflects the most recent available trading day (typically previous business day).
Series used:
  DFEDTARL / DFEDTARU — Fed Funds target lower/upper bound
  DGS3MO  — 3-Month Treasury yield
  DGS2    — 2-Year Treasury yield
  DGS10   — 10-Year Treasury yield
"""

import logging

import requests

log = logging.getLogger(__name__)

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_SERIES = {
    "DFEDTARL": "Fed Funds lower bound",
    "DFEDTARU": "Fed Funds upper bound",
    "DGS3MO":   "3-Month T-Bill yield",
    "DGS2":     "2-Year yield",
    "DGS10":    "10-Year yield",
}


def _fred_latest(series_id: str) -> tuple[float | None, str | None]:
    """Fetch the most recent non-null value and its date from a FRED series."""
    try:
        resp = requests.get(_FRED_CSV, params={"id": series_id}, timeout=10)
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        # Rows are chronological; scan from the end for latest non-missing value
        for row in reversed(lines[1:]):
            parts = row.split(",")
            if len(parts) == 2 and parts[1].strip() not in ("", "."):
                return float(parts[1].strip()), parts[0].strip()
    except Exception as exc:
        log.warning("FRED fetch failed for %s: %s", series_id, exc)
    return None, None


def fetch_rates_data() -> str:
    """Return a formatted yield curve + Fed Funds context string for the LLM prompt."""
    vals: dict[str, float | None] = {}
    dates: dict[str, str] = {}

    for sid in _SERIES:
        v, d = _fred_latest(sid)
        vals[sid] = v
        if d:
            dates[sid] = d

    fed_lo = vals.get("DFEDTARL")
    fed_hi = vals.get("DFEDTARU")
    y3m    = vals.get("DGS3MO")
    y2     = vals.get("DGS2")
    y10    = vals.get("DGS10")

    as_of = max(dates.values()) if dates else "last trading day"
    lines = [f"_Rates as of {as_of} — FRED (Federal Reserve Economic Data)_\n"]

    # Fed Funds target range
    if fed_lo is not None and fed_hi is not None:
        lines.append(f"**Fed Funds Target Range:** {fed_lo:.2f}% – {fed_hi:.2f}%")
    elif fed_hi is not None:
        lines.append(f"**Fed Funds Target Rate:** {fed_hi:.2f}%")
    else:
        lines.append("**Fed Funds Target Rate:** unavailable")

    # Treasury yield table
    rows = []
    for label, sid in [("3-Month", "DGS3MO"), ("2-Year", "DGS2"), ("10-Year", "DGS10")]:
        val = vals.get(sid)
        if val is None:
            rows.append(f"| {label} | — | — |")
        elif fed_hi is not None:
            diff = val - fed_hi
            sign = "+" if diff >= 0 else ""
            rows.append(f"| {label} | {val:.2f}% | {sign}{diff:.2f}% |")
        else:
            rows.append(f"| {label} | {val:.2f}% | — |")

    lines += [
        "",
        "| Tenor    | Yield  | vs. Fed Funds |",
        "|:---------|-------:|--------------:|",
    ] + rows

    # 2Y–10Y spread with inversion flag
    if y2 is not None and y10 is not None:
        spread_bps = round((y10 - y2) * 100)
        if spread_bps < 0:
            curve_note = (
                f"⚠️ **Yield curve INVERTED** (2Y–10Y = {spread_bps:+d}bps) — "
                "historically precedes recession by 12–18 months"
            )
        elif spread_bps < 25:
            curve_note = f"Yield curve nearly flat (2Y–10Y = {spread_bps:+d}bps)"
        else:
            curve_note = f"Yield curve normal (2Y–10Y = {spread_bps:+d}bps)"
        lines.append(f"\n{curve_note}")

    # Implied near-term rate expectations from 3M T-Bill vs upper target
    if fed_hi is not None and y3m is not None:
        diff_bps = round((y3m - fed_hi) * 100)
        if diff_bps <= -50:
            expectation = (
                f"3M T-Bill is {abs(diff_bps)}bps below Fed upper target — "
                "market pricing multiple near-term cuts"
            )
        elif diff_bps <= -25:
            expectation = (
                f"3M T-Bill is {abs(diff_bps)}bps below Fed upper target — "
                "1–2 cuts priced in near term"
            )
        elif diff_bps <= -10:
            expectation = (
                f"3M T-Bill is {abs(diff_bps)}bps below Fed upper target — "
                "modest cut expectations"
            )
        else:
            expectation = (
                f"3M T-Bill near Fed upper target ({diff_bps:+d}bps) — "
                "minimal near-term cut expectations priced"
            )
        lines.append(f"**Implied Rate Expectation:** {expectation}")
        lines.append(
            "_Note: For precise meeting probabilities, cross-reference CME FedWatch tool._"
        )

    return "\n".join(lines)
