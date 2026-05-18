#!/usr/bin/env python3
"""
Tech Analyst
Reads raw_data/tech_YYYY-MM-DD.json → LLM → email

Usage:
  python3 tech_analyst.py        # full run
  python3 tech_analyst.py --test # skip LLM, send [TEST] email
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import ACTIVE_MODEL, LLM_ENGINE
from lib.llm import run_llm
from lib.email_report import render_html, send_email
from lib.report_store import save_report, load_previous_report
from lib.prompt_loader import PromptLoader
from lib.raw_store import load_raw
from lib.watchlist import get_stocks
from lib.entry_signals import format_entry_section

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _format_tech_stocks(tech_stocks: list[dict]) -> str:
    if not tech_stocks:
        return "No tech stock data available."
    header = ("| Ticker | Price | 1D% | RSI | ATR | 均线排列 | vs EMA20% | vs MA60% | vs MA200% | POC | 周线 | 5日量比 |")
    sep    = ("|:-------|------:|----:|----:|----:|:---------|----------:|---------:|----------:|----:|:-----|:-------|")
    rows = []
    for s in tech_stocks:
        align_short = {"bullish": "多头✅", "bearish": "空头⚠️", "mixed": "混合"}.get(
            s.get("alignment", "mixed"), "混合")
        vs_ema20 = round((s["price"] / s["ema_20"] - 1) * 100, 1) if s.get("ema_20") else 0
        vs_ma60  = round((s["price"] / s["ma_60"]  - 1) * 100, 1) if s.get("ma_60")  else 0
        vs_ma200 = s.get("vs_ma200_pct") or 0
        poc      = s.get("poc")
        w        = s.get("weekly") or {}
        w_str    = w.get("w_alignment", "N/A") if w else "N/A"
        vols     = s.get("vol_5d_ratios", [])
        vol_str  = ",".join(str(v) for v in vols[-3:]) if vols else "N/A"
        rows.append(
            f"| {s['ticker']:<5} "
            f"| ${s['price']:>8.2f} | {s['change_1d_pct']:>+5.2f}% "
            f"| {s['rsi_14']:>4.0f} | {s['atr_14']:>5.2f} "
            f"| {align_short:<8} "
            f"| {vs_ema20:>+8.1f}% | {vs_ma60:>+7.1f}% | {vs_ma200:>+8.1f}% "
            f"| {'${:.0f}'.format(poc) if poc else 'N/A'} "
            f"| {w_str:<8} | {vol_str} |"
        )
    return "\n".join([header, sep] + rows)


def _build_thesis_section() -> list[str]:
    """Inject investment thesis + key risks from watchlist.yaml into the prompt.

    The LLM uses this to evaluate whether today's news supports, challenges,
    or is neutral to each position's thesis — not just describe price moves.
    """
    lines = ["=" * 60,
             "SECTION 0 — INVESTMENT THESIS & RISK REGISTER (from watchlist.yaml)",
             "=" * 60,
             "For each stock below, evaluate today's news against the stated thesis.",
             "Explicitly flag if any development SUPPORTS ✅, CHALLENGES ⚠️, or is",
             "NEUTRAL ➡️ to the thesis. Flag any news that materialises a listed key risk.",
             ""]
    for stock in get_stocks("all"):
        tier  = "core" if stock in get_stocks("core") else "watchlist"
        label = stock["ticker"]
        if tier == "watchlist":
            upgrade = stock.get("upgrade_condition", "")
            label  += f" (watchlist — upgrade if: {upgrade})"
        else:
            label += " (core position)"

        lines.append(f"{label}")
        lines.append(f"  Thesis: {stock['thesis'].strip()}")
        risks = stock.get("key_risks", [])
        if risks:
            lines.append("  Key Risks:")
            for r in risks:
                lines.append(f"    - {r}")
        lines.append("")
    return lines


def build_user_message(data: dict, prev_report: str | None, macro_regime: str | None) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"Today is {today}. Below are the latest AI & tech sector inputs:\n"]
    lines += _build_thesis_section()

    tech_stocks = data.get("tech_stocks", [])
    lines += ["=" * 60,
              "SECTION A — AI/TECH STOCK PRICES & TECHNICALS (raw numbers, yfinance)",
              "=" * 60, _format_tech_stocks(tech_stocks), ""]

    # Surface raw numbers for the LLM to reason about directly
    if tech_stocks:
        lines += ["Raw data per stock (extended — includes Lei trading system fields):"]
        for s in tech_stocks:
            w = s.get("weekly") or {}
            lines.append(
                f"  {s['ticker']}: price={s['price']}, rsi={s['rsi_14']}, atr={s['atr_14']}"
                f"\n    MAs: ma20={s['ma_20']}, ma50={s['ma_50']}, ma60={s.get('ma_60')}, "
                f"ma120={s.get('ma_120')}, ma200={s.get('ma_200')}"
                f"\n    EMAs: ema20={s.get('ema_20')}, ema60={s.get('ema_60')}"
                f"\n    Alignment: {s.get('alignment')} | dense_zone={s.get('ma_dense_zone')} "
                f"(spread={s.get('ma_spread_pct')}%)"
                f"\n    Slopes (抵扣价): ma20={s.get('ma20_slope_pos')}, "
                f"ma60={s.get('ma60_slope_pos')}, ma120={s.get('ma120_slope_pos')}"
                f"\n    Volume: today={s.get('vol_today')}, avg20={s.get('vol_avg_20')}, "
                f"ratio={s.get('vol_ratio')} | 5d_ratios={s.get('vol_5d_ratios')}"
                f"\n    POC={s.get('poc')}, VAH={s.get('vah')}, VAL={s.get('val')}"
                f"\n    Stop ref (10d low)={s.get('low_10d')}"
                f"\n    Weekly: {w.get('w_alignment')} | w_ma20={w.get('w_ma20')}, "
                f"w_ma60={w.get('w_ma60')} | slopes: ma20={w.get('w_ma20_slope_pos')}, "
                f"ma60={w.get('w_ma60_slope_pos')}"
                f"\n    52w: high={s['high_52w']}, low={s['low_52w']}, "
                f"pct_off_high={s['pct_off_52w_high']}%"
            )
        lines.append("")

    # SECTION X: pre-computed Lei entry signals (deterministic numbers for the LLM)
    if tech_stocks:
        lines += ["", format_entry_section(tech_stocks), ""]

    lines += ["=" * 60, "SECTION B — NEWS & TECH SECTOR CONTEXT (past 24 hours)", "=" * 60]
    for i, item in enumerate(data.get("news_items", []), 1):
        lines.append(f"\n--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    if macro_regime:
        lines += ["", "=" * 60,
                  "SECTION C — MACRO REGIME CONTEXT (from macro_analyst, use as backdrop)",
                  "=" * 60, macro_regime]

    if prev_report:
        lines += ["", "=" * 60,
                  "SECTION D — PREVIOUS DAY'S TECH ANALYSIS BASELINE (continuity context)",
                  "=" * 60, prev_report]

    lines.append(
        "\nApply the materiality scoring first, then produce the full analysis "
        "for every development that scores 7 or higher."
    )
    return "\n".join(lines)


def _parse_bilingual(response: str) -> tuple[str, str]:
    marker = "[BEGIN_CHINESE_TRANSLATION]"
    if marker not in response:
        log.warning("Bilingual marker not found — email will render English only")
        return response.strip(), response.strip()
    eng, chn = response.split(marker, 1)
    return eng.strip(), chn.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tech Analyst")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Tech Analyst %s%s (engine: %s) ===",
             today_str, " (TEST)" if args.test else "", LLM_ENGINE)

    data = load_raw("tech")
    if not data:
        log.error("No tech raw data for today. Run: python3 data_fetcher/fetch_tech.py")
        return

    prev_report  = None if args.test else load_previous_report("tech")
    macro_regime = None if args.test else load_previous_report("macro")

    if args.test:
        english = f"[TEST MODE] {LLM_ENGINE} inference skipped. {len(data.get('news_items', []))} news items in raw data."
        chinese = f"[测试模式] 已跳过 {LLM_ENGINE} 推理。"
    else:
        prompt   = PromptLoader.load("tech", "analysis")
        user_msg = build_user_message(data, prev_report, macro_regime)
        response = run_llm(prompt, user_msg, label="tech_analysis")
        log.info("LLM response complete (%d chars)", len(response))
        english, chinese = _parse_bilingual(response)
        save_report("tech", english)

    news_items = data.get("news_items", [])
    html       = render_html(chinese, news_items, ACTIVE_MODEL,
                             title_emoji="🤖", title_text="AI科技行业分析报告", style="macro")
    subject    = f"{subject_prefix}【宏观AI实验室】AI科技行业报告 — {today_str}"
    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
