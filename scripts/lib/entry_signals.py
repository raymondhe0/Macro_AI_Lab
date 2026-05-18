"""Lei 价量时空 entry signal computation and formatting.

Shared by tech_analyst.py (pipeline) and entry_analyst.py (ad-hoc).
"""

from __future__ import annotations

import logging
import math

import numpy as np
import yfinance as yf
from stockstats import wrap as ss_wrap

from lib.stock_data import _compute_volume_profile, _fetch_weekly_summary

log = logging.getLogger(__name__)


# ── Single-stock Lei data fetch ────────────────────────────────────────────────

def fetch_single_lei_stock(ticker: str) -> dict | None:
    """Fetch all Lei trading system fields for any ticker via yfinance.

    Returns a dict with the same schema as fetch_tech_stock_data() records,
    so compute_lei_signals() works on both pipeline and ad-hoc data.
    """
    try:
        tkr  = yf.Ticker(ticker)
        info = tkr.info or {}
        hist = tkr.history(period="2y", interval="1d")
        if hist.empty or len(hist) < 52:
            log.warning("Insufficient history for %s", ticker)
            return None

        price  = round(float(hist["Close"].iloc[-1]), 2)
        prev   = round(float(hist["Close"].iloc[-2]), 2)
        chg_1d = round((price / prev - 1) * 100, 2)

        hist_1y = hist.iloc[-252:] if len(hist) >= 252 else hist
        high_52w = round(float(hist_1y["High"].max()), 2)
        low_52w  = round(float(hist_1y["Low"].min()), 2)
        high_2y  = round(float(hist["High"].max()), 2)
        low_2y   = round(float(hist["Low"].min()), 2)
        pct_52w  = round((price - low_52w) / (high_52w - low_52w), 3) if high_52w != low_52w else None
        pct_2y   = round((price - low_2y)  / (high_2y  - low_2y),  3) if high_2y  != low_2y  else None

        ma_20  = round(float(hist["Close"].rolling(20).mean().iloc[-1]), 2)
        ma_50  = round(float(hist["Close"].rolling(50).mean().iloc[-1]), 2)

        def _ma(n):
            v = hist["Close"].rolling(n).mean().iloc[-1]
            return round(float(v), 2) if not math.isnan(v) else None

        ma_60  = _ma(60)
        ma_120 = _ma(120)
        ma_200 = _ma(200)

        ema_20  = round(float(hist["Close"].ewm(span=20,  adjust=False).mean().iloc[-1]), 2)
        ema_60  = round(float(hist["Close"].ewm(span=60,  adjust=False).mean().iloc[-1]), 2)
        ema_120 = round(float(hist["Close"].ewm(span=120, adjust=False).mean().iloc[-1]), 2)

        cs = hist["Close"]
        ma20_slope  = bool(cs.iloc[-1] > cs.iloc[-21])  if len(cs) >= 21  else None
        ma60_slope  = bool(cs.iloc[-1] > cs.iloc[-61])  if len(cs) >= 61  else None
        ma120_slope = bool(cs.iloc[-1] > cs.iloc[-121]) if len(cs) >= 121 else None

        _mas = [v for v in [ma_20, ma_60, ma_120] if v is not None]
        bullish = len(_mas) == 3 and price > ma_20 > ma_60 > ma_120
        bearish = len(_mas) == 3 and price < ma_20 < ma_60 < ma_120
        alignment = "bullish" if bullish else "bearish" if bearish else "mixed"
        ma_spread = ((max(_mas) - min(_mas)) / min(_mas) * 100) if len(_mas) >= 2 else None
        ma_dense  = ma_spread is not None and ma_spread < 2.0

        vol_today  = round(float(hist["Volume"].iloc[-1]), 0)
        vol_avg_20 = round(float(hist["Volume"].rolling(20).mean().iloc[-1]), 0)
        vol_ratio  = round(vol_today / vol_avg_20, 2) if vol_avg_20 > 0 else None
        vol_5d_ratios = (
            [round(float(v) / vol_avg_20, 2) for v in hist["Volume"].iloc[-5:]]
            if vol_avg_20 > 0 and len(hist) >= 5 else []
        )
        low_10d = round(float(hist["Low"].iloc[-10:].min()), 2) if len(hist) >= 10 else None

        poc, vah, val_area = _compute_volume_profile(hist)
        w = _fetch_weekly_summary(hist)

        hist_r = hist.reset_index()
        hist_r.columns = [c.lower() for c in hist_r.columns]
        ss  = ss_wrap(hist_r)
        rsi = round(float(ss["rsi"].iloc[-1]), 1)
        atr = round(float(ss["atr"].iloc[-1]), 2)

        company = info.get("longName", ticker)
        sector  = info.get("sector", "N/A")

        return {
            "ticker":      ticker,
            "description": f"{company} — {sector}",
            "price":             price,
            "prev_close":        prev,
            "change_1d_pct":     chg_1d,
            "high_52w":          high_52w,
            "low_52w":           low_52w,
            "pct_off_52w_high":  round((price / high_52w - 1) * 100, 2),
            "price_pct_52w_range": pct_52w,
            "price_pct_2y_range":  pct_2y,
            "ma_20":   ma_20,
            "ma_50":   ma_50,
            "ma_60":   ma_60,
            "ma_120":  ma_120,
            "ma_200":  ma_200,
            "ema_20":  ema_20,
            "ema_60":  ema_60,
            "ema_120": ema_120,
            "vs_ma50_pct":  round((price / ma_50 - 1) * 100, 2),
            "vs_ma200_pct": round((price / ma_200 - 1) * 100, 2) if ma_200 else None,
            "above_ma50":   price > ma_50,
            "above_ma200":  ma_200 is not None and price > ma_200,
            "ma20_slope_pos":  ma20_slope,
            "ma60_slope_pos":  ma60_slope,
            "ma120_slope_pos": ma120_slope,
            "alignment":       alignment,
            "ma_dense_zone":   ma_dense,
            "ma_spread_pct":   round(ma_spread, 1) if ma_spread is not None else None,
            "vol_today":    int(vol_today),
            "vol_avg_20":   int(vol_avg_20),
            "vol_ratio":    vol_ratio,
            "vol_5d_ratios": vol_5d_ratios,
            "low_10d":      low_10d,
            "poc":  poc,
            "vah":  vah,
            "val":  val_area,
            "weekly": w,
            "rsi_14":  rsi,
            "atr_14":  atr,
        }
    except Exception as exc:
        log.warning("Lei data fetch failed for %s: %s", ticker, exc)
        return None


# ── Signal computation ─────────────────────────────────────────────────────────

def compute_lei_signals(s: dict) -> dict:
    """Compute the 9 Lei entry conditions + stop/target/R:R from a stock record."""
    price   = s["price"]
    ema_20  = s.get("ema_20")
    ma_120  = s.get("ma_120")
    ma_dense = s.get("ma_dense_zone", False)
    ma20_slope = s.get("ma20_slope_pos")
    vol_ratio  = s.get("vol_ratio")
    poc     = s.get("poc")
    low_10d = s.get("low_10d")
    atr     = s.get("atr_14")
    high_52w = s.get("high_52w")

    weekly = s.get("weekly") or {}
    w_ma20_slope = weekly.get("w_ma20_slope_pos")
    w_alignment  = weekly.get("w_alignment", "")

    # 9 conditions (prompt calls them "8" — count is ~9 due to two volume gates)
    c1 = bool(w_ma20_slope)                          # 周线MA20斜率正
    c2 = w_alignment == "多头排列"                   # 周线多头排列
    c3 = bool(ema_20 and price > ema_20)             # 价格站上EMA20
    c4 = bool(ma20_slope)                            # MA20斜率正 (抵扣价)
    c5 = bool(ma_120 and price > ma_120)             # 价格站上MA120
    c6 = not bool(ma_dense)                          # 非均线密集区
    c7 = bool(vol_ratio and vol_ratio < 0.7)         # 回调缩量
    c8 = bool(vol_ratio and vol_ratio > 1.5)         # 突破放量
    c9 = bool(poc and price > poc)                   # 价格站上POC

    passed = sum([c1, c2, c3, c4, c5, c6, c7, c8, c9])

    # Stop: 10d low − 0.5 × ATR
    stop = round(low_10d - 0.5 * atr, 2) if (low_10d and atr) else None

    # T1: nearest significant resistance above current price
    resistances: list[tuple[str, float]] = []
    if ma_120 and ma_120 > price:
        resistances.append(("MA120", ma_120))
    if high_52w and high_52w * 0.98 > price:
        resistances.append(("52w-high×0.98", round(high_52w * 0.98, 2)))
    if resistances:
        t1_label, t1 = min(resistances, key=lambda x: x[1])
    else:
        t1_label = "+8% target"
        t1 = round(price * 1.08, 2)

    # T2: further target (52w high or +15%)
    t2 = round(high_52w, 2) if (high_52w and high_52w > t1) else round(price * 1.15, 2)

    def _rr(entry: float, target: float, stop_: float | None) -> float | None:
        if stop_ is None or entry <= stop_:
            return None
        return round((target - entry) / (entry - stop_), 2)

    rr_now  = _rr(price, t1, stop)
    wait_entry = ema_20 if (ema_20 and ema_20 < price * 0.99) else None
    rr_wait = _rr(wait_entry, t1, stop) if wait_entry else None

    return {
        "c1_w_ma20_slope":  c1,
        "c2_w_bullish":     c2,
        "c3_above_ema20":   c3,
        "c4_ma20_slope":    c4,
        "c5_above_ma120":   c5,
        "c6_not_dense":     c6,
        "c7_pullback_vol":  c7,
        "c8_breakout_vol":  c8,
        "c9_above_poc":     c9,
        "conditions_met":   passed,
        "stop":             stop,
        "t1":               t1,
        "t1_label":         t1_label,
        "t2":               t2,
        "rr_now":           rr_now,
        "entry_now":        price,
        "entry_wait":       wait_entry,
        "rr_wait":          rr_wait,
    }


# ── SECTION X formatter (injected into LLM user message) ──────────────────────

def format_entry_section(tech_stocks: list[dict]) -> str:
    """Format the Lei entry pre-computation block (SECTION X) for LLM injection.

    The LLM must not re-derive these numbers — it reads them directly and uses them
    to fill in the 4-section entry output in Recommended Positioning.
    """
    lines = [
        "=" * 60,
        "SECTION X — LEI ENTRY SIGNAL PRE-COMPUTATION (deterministic)",
        "=" * 60,
        "Pre-computed entry signal values for each covered stock.",
        "Use EXACTLY these numbers in Step 3 Recommended Positioning.",
        "Do NOT recalculate — fill in the 4-section block from these values.",
        "",
    ]

    def _ck(val: bool) -> str:
        return "✅" if val else "☐"

    def _rr_flag(rr: float | None) -> str:
        if rr is None:
            return "N/A"
        return f"{rr:.1f}:1 {'⚠️ <2:1' if rr < 2.0 else '✅'}"

    for s in tech_stocks:
        sig  = compute_lei_signals(s)
        w    = s.get("weekly") or {}
        vols = s.get("vol_5d_ratios", [])
        vol_str = ", ".join(str(v) for v in vols) if vols else "N/A"

        lines += [
            f"─── {s['ticker']} (price=${s['price']}, ATR=${s.get('atr_14')}) ───",
            "",
            "Lei 9-condition checklist (need ≥4 to enter):",
            f"  W1 周线MA20斜率正  w_ma20_slope_pos={w.get('w_ma20_slope_pos')}  {_ck(sig['c1_w_ma20_slope'])}",
            f"  W2 周线多头排列    w_alignment={w.get('w_alignment')}  {_ck(sig['c2_w_bullish'])}",
            f"  D1 价格站上EMA20   price=${s['price']} vs ema20=${s.get('ema_20')}  {_ck(sig['c3_above_ema20'])}",
            f"  D2 MA20斜率正      ma20_slope_pos={s.get('ma20_slope_pos')}  {_ck(sig['c4_ma20_slope'])}",
            f"  D3 价格站上MA120   price=${s['price']} vs ma120=${s.get('ma_120')}  {_ck(sig['c5_above_ma120'])}",
            f"  D4 非均线密集区    ma_dense={s.get('ma_dense_zone')} spread={s.get('ma_spread_pct')}%  {_ck(sig['c6_not_dense'])}",
            f"  V1 回调缩量        vol_ratio={s.get('vol_ratio')} < 0.7  {_ck(sig['c7_pullback_vol'])}",
            f"  V2 突破放量        vol_ratio={s.get('vol_ratio')} > 1.5  {_ck(sig['c8_breakout_vol'])}",
            f"  S1 价格站上POC     price=${s['price']} vs poc=${s.get('poc')}  {_ck(sig['c9_above_poc'])}",
            f"  → CONDITIONS MET: {sig['conditions_met']}/9  "
            + ("✅ ENTRY VALID" if sig['conditions_met'] >= 4 else "⚠️ BELOW THRESHOLD"),
            "",
            f"Key levels:",
            f"  low_10d=${s.get('low_10d')}  ATR=${s.get('atr_14')}",
            f"  Stop = ${sig['stop']} (low_10d − 0.5×ATR)",
            f"  T1   = ${sig['t1']} ({sig['t1_label']})",
            f"  T2   = ${sig['t2']} (extended target)",
            f"  EMA20=${s.get('ema_20')}  MA60=${s.get('ma_60')}  MA120={s.get('ma_120')}  POC=${s.get('poc')}",
            f"  Weekly: {w.get('w_alignment')} | w_ma20=${w.get('w_ma20')} w_ma60=${w.get('w_ma60')}",
            f"  5d vol ratios: {vol_str}",
            "",
            f"R:R comparison:",
            f"  Buy Now   entry=${sig['entry_now']}  stop=${sig['stop']}  T1=${sig['t1']}  R:R={_rr_flag(sig['rr_now'])}",
        ]
        if sig["entry_wait"]:
            lines.append(
                f"  Wait EMA20 entry=${sig['entry_wait']}  stop=${sig['stop']}  T1=${sig['t1']}  R:R={_rr_flag(sig['rr_wait'])}"
            )
        else:
            lines.append("  Wait EMA20: price already at/below EMA20 — no wait scenario")
        lines.append("")

    return "\n".join(lines)
