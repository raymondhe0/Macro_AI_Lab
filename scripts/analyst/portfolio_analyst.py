#!/usr/bin/env python3
"""
Portfolio Analyst
Reads ALL raw_data/*_YYYY-MM-DD.json → single LLM call → email

This is the only analyst that sees all raw signals simultaneously,
with zero information decay from inter-LLM summarisation.

Usage:
  python3 portfolio_analyst.py        # full run
  python3 portfolio_analyst.py --test # skip LLM, send [TEST] email
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf
from stockstats import wrap as ss_wrap

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.report_store import save_report
from lib.prompt_loader import PromptLoader
from lib.raw_store import load_all_raw_today
from lib.watchlist import get_stocks

# ── Portfolio universe ────────────────────────────────────────────────────────

_ETF_UNIVERSE: dict[str, str] = {
    "QQQ": "Nasdaq 100 ETF (tech / growth proxy)",
    "VOO": "S&P 500 ETF (broad market proxy)",
    "GLD": "Gold ETF (safe-haven / inflation hedge)",
    "TLT": "20+ Year Treasury ETF (duration / risk-off hedge)",
}


def _get_portfolio_tickers() -> dict[str, str]:
    """ETFs + all watchlist stocks (core + watchlist tier), sourced from watchlist.yaml."""
    stocks = {s["ticker"]: s["name"] for s in get_stocks("all")}
    return {**_ETF_UNIVERSE, **stocks}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Portfolio price snapshot ──────────────────────────────────────────────────

def fetch_portfolio_prices() -> str:
    rows = []
    for ticker, label in _get_portfolio_tickers().items():
        try:
            hist = yf.Ticker(ticker).history(period="1y", interval="1d")
            if hist.empty or len(hist) < 200:
                rows.append(f"| {ticker:<5} | {label:<45} | N/A | — | — | — | — | — | — | — |")
                continue
            price      = hist["Close"].iloc[-1]
            pct_1d     = (price / hist["Close"].iloc[-2] - 1) * 100
            ma50       = hist["Close"].rolling(50).mean().iloc[-1]
            ma200      = hist["Close"].rolling(200).mean().iloc[-1]
            vs_50      = (price / ma50 - 1) * 100
            vs_200     = (price / ma200 - 1) * 100
            high_52w   = hist["High"].max()
            off_peak   = (price / high_52w - 1) * 100
            hist_r     = hist.reset_index()
            hist_r.columns = [c.lower() for c in hist_r.columns]
            ss  = ss_wrap(hist_r)
            rsi = float(ss["rsi"].iloc[-1])
            atr = float(ss["atr"].iloc[-1])
            trend = "Above 50MA ▲" if price > ma50 else "Below 50MA ▼"
            rows.append(
                f"| {ticker:<5} | {label:<45} | ${price:>9.2f} | {pct_1d:>+6.2f}% "
                f"| RSI {rsi:>4.0f} | ATR {atr:>6.2f} | {trend} "
                f"| vs MA50: {vs_50:>+5.1f}% | vs MA200: {vs_200:>+5.1f}% "
                f"| 52w off peak: {off_peak:>+5.1f}% |"
            )
        except Exception as exc:
            log.warning("Price fetch failed for %s: %s", ticker, exc)
            rows.append(f"| {ticker:<5} | {label:<45} | ERROR | — | — | — | — | — | — | — |")

    header = ("| Ticker | Instrument | Price | 1D Chg | RSI(14) | ATR(14) "
              "| Trend | vs MA50 | vs MA200 | 52w off peak |")
    sep    = ("|:-------|:-----------|------:|-------:|--------:|--------:"
              "|:------|--------:|---------:|-------------:|")
    return "\n".join([header, sep] + rows)


# ── Previous allocation loader ────────────────────────────────────────────────

def _load_prev_allocation() -> str | None:
    """Extract Step 4 allocation table + Step 6 CIO Summary from the most recent portfolio report."""
    reports_dir = Path(__file__).parent.parent.parent / "reports"
    if not reports_dir.exists():
        return None
    today_stem = f"portfolio_{datetime.now().strftime('%Y-%m-%d')}"
    candidates = sorted(
        (p for p in reports_dir.glob("portfolio_*.md") if p.stem != today_stem),
        key=lambda p: p.stem,
        reverse=True,
    )
    if not candidates:
        log.info("No previous portfolio report found")
        return None
    try:
        text = candidates[0].read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("Could not read previous portfolio report: %s", exc)
        return None
    sections = []
    for start_marker, end_marker in [
        ("## STEP 4 — PORTFOLIO ALLOCATION TABLE", "## STEP 5"),
        ("## STEP 6 — CIO SUMMARY", "## STEP 7"),
    ]:
        s = text.find(start_marker)
        if s < 0:
            continue
        e = text.find(end_marker, s + len(start_marker))
        sections.append(text[s : e if e > 0 else s + 2000].strip())
    if not sections:
        log.warning("Previous portfolio report found but Step 4/6 markers missing — skipping baseline")
        return None
    log.info("Loaded previous portfolio allocation from %s", candidates[0].name)
    return "\n\n".join(sections)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _format_news_block(news_items: list[dict], offset: int = 1) -> list[str]:
    lines = []
    for i, item in enumerate(news_items, offset):
        lines.append(f"\n--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")
    return lines


def _build_thesis_section() -> list[str]:
    """Inject the full watchlist — thesis, risks, and upgrade conditions.

    The portfolio_analyst uses this as its mandate: which positions are core
    convictions vs. monitored candidates, and what each thesis requires to hold.
    """
    lines = ["=" * 60,
             "SECTION 0 — PORTFOLIO MANDATE: THESIS & RISK REGISTER (from watchlist.yaml)",
             "=" * 60,
             "This is your investment mandate. Use it to:",
             "  1. Evaluate whether today's data SUPPORTS ✅, CHALLENGES ⚠️, or is NEUTRAL ➡️",
             "     to each position's thesis.",
             "  2. Flag any development that materialises a listed key risk.",
             "  3. For watchlist stocks, assess whether the upgrade condition has been met.",
             ""]

    lines.append("── CORE POSITIONS (fully deployed, held on thesis) ──")
    for s in get_stocks("core"):
        lines.append(f"\n{s['ticker']} ({s['name']}) · Layer: {s['layer']}")
        lines.append(f"  Thesis: {s['thesis'].strip()}")
        for r in s.get("key_risks", []):
            lines.append(f"  Risk: {r}")

    lines.append("\n── WATCHLIST (monitoring for catalyst, not yet core) ──")
    for s in get_stocks("watchlist"):
        lines.append(f"\n{s['ticker']} ({s['name']}) · Layer: {s['layer']}")
        lines.append(f"  Thesis: {s['thesis'].strip()}")
        lines.append(f"  Upgrade if: {s.get('upgrade_condition', 'N/A')}")
        for r in s.get("key_risks", []):
            lines.append(f"  Risk: {r}")

    lines.append("")
    return lines


def build_user_message(
    portfolio_prices: str,
    all_raw: dict[str, dict],
    prev_allocation: str | None = None,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"Today is {today}.\n"]
    lines += _build_thesis_section()

    lines += ["=" * 60,
              "SECTION A — PORTFOLIO UNIVERSE: LIVE PRICES & TECHNICALS (yfinance, fetched now)",
              "=" * 60, portfolio_prices, ""]

    # ── Macro raw data ──
    macro = all_raw.get("macro")
    if macro:
        lines += ["=" * 60,
                  "SECTION B — MACRO: PRICES, RATES, BREADTH, EARNINGS (raw from Finnhub/FRED/Yahoo)",
                  "=" * 60,
                  "── PRICES: ETF SPOT PRICES (Finnhub) ──",
                  macro.get("prices_md", ""), "",
                  "── RATES: YIELD CURVE & FED FUNDS RATE (FRED) ──",
                  macro.get("rates_md", ""), "",
                  "── MARKET BREADTH (ETF MA structure + RSP÷SPY quality; all above 200d=bull, RSP÷SPY falling=narrow rally) ──",
                  macro.get("breadth_md", "N/A"), "",
                  "── EARNINGS: MAJOR EARNINGS THIS WEEK (Finnhub) ──",
                  macro.get("earnings_md", ""), ""]
        lines += ["=" * 60, "SECTION B2 — MACRO NEWS (raw articles, unfiltered by LLM)", "=" * 60]
        lines += _format_news_block(macro.get("news_items", []))
        lines.append("")

    # ── Tech raw data ──
    tech = all_raw.get("tech")
    if tech:
        lines += ["=" * 60,
                  "SECTION C — AI/TECH SECTOR NEWS (raw articles, unfiltered by LLM)",
                  "=" * 60]
        lines += _format_news_block(tech.get("news_items", []))
        lines.append("")

    # ── Stock raw data ──
    stock = all_raw.get("stock")
    if stock:
        tickers = stock.get("tickers", [])
        lines += ["=" * 60,
                  f"SECTION D — STOCK DATA: {', '.join(tickers)} (yfinance fundamentals + technicals)",
                  "=" * 60, stock.get("stock_data_md", ""), ""]
        lines += ["=" * 60, "SECTION D2 — STOCK NEWS (raw articles)", "=" * 60]
        lines += _format_news_block(stock.get("news_items", []))
        lines.append("")

    # ── Trading raw data ──
    for mode in ("intraday", "weekly"):
        trading = all_raw.get(f"trading_{mode}")
        if trading:
            tag = "E-INTRADAY" if mode == "intraday" else "E-WEEKLY"
            lines += ["=" * 60,
                      f"SECTION {tag} — TECHNICAL LEVELS: NQ & GC (yfinance)",
                      "=" * 60, trading.get("tech_levels_md", ""), ""]
            lines += ["=" * 60, f"SECTION {tag}-NEWS — TRADING NEWS (raw articles)", "=" * 60]
            lines += _format_news_block(trading.get("news_items", []))
            lines.append("")

    if not all_raw:
        lines += ["=" * 60,
                  "NOTE: No raw data files found for today.",
                  "Run the data fetchers first, then re-run this analyst.",
                  "=" * 60, ""]

    if prev_allocation:
        lines += ["=" * 60,
                  "SECTION F — PREVIOUS DAY'S ALLOCATION (Step 4 table + Step 6 CIO Summary)",
                  "Use this as your baseline: note what has changed vs. yesterday's regime, "
                  "conviction levels, and position sizes. Flag any drift explicitly.",
                  "=" * 60,
                  prev_allocation, ""]

    lines.append(
        "\nAll data sections above (0, A, B, B2, C, D, D2, E-INTRADAY, E-WEEKLY) contain raw, unprocessed market data — no prior LLM has touched them. "
        "Section F (if present) is the previous day's LLM-generated allocation — treat it as a baseline "
        "for drift comparison, not as a source of market facts. "
        "Produce the Portfolio Synthesis Report following your system prompt exactly."
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
    parser = argparse.ArgumentParser(description="Portfolio Analyst")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Portfolio Analyst %s%s (engine: %s) ===",
             today_str, " (TEST)" if args.test else "", LLM_ENGINE)

    log.info("Fetching live portfolio prices...")
    portfolio_prices = fetch_portfolio_prices()

    all_raw = {} if args.test else load_all_raw_today()
    if all_raw:
        log.info("Raw data sources available: %s", list(all_raw.keys()))

    prev_allocation = _load_prev_allocation()
    if prev_allocation:
        log.info("Previous allocation loaded (%d chars)", len(prev_allocation))

    if args.test:
        english = (
            f"[TEST MODE] {LLM_ENGINE} inference skipped.\n\n"
            f"Portfolio prices fetched successfully:\n{portfolio_prices}"
        )
        chinese = "[测试模式] 已跳过推理。组合价格已成功获取。"
    else:
        prompt   = PromptLoader.load("portfolio", "allocation")
        user_msg = build_user_message(portfolio_prices, all_raw, prev_allocation)
        response = run_llm(prompt, user_msg, label="portfolio_allocation")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)
        save_report("portfolio", english)

    html    = render_html(chinese, [], ACTIVE_MODEL,
                          title_emoji="💼", title_text="组合配置报告", style="macro")
    subject = f"{subject_prefix}【宏观AI实验室】组合配置报告 — {today_str}"
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
