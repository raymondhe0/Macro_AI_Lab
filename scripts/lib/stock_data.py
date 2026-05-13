"""Individual stock price snapshot, fundamentals, and technical levels via yfinance."""

import logging
import math
from datetime import datetime

import numpy as np
import yfinance as yf
from stockstats import wrap as ss_wrap

log = logging.getLogger(__name__)


def _compute_volume_profile(hist, n_bins: int = 200) -> tuple[float, float, float]:
    """Approximate Volume Profile from OHLCV history.

    Returns (poc_price, value_area_high, value_area_low).
    Uses the last 250 trading days and distributes each day's volume
    proportionally across its high-low range (Lei's 筹码分布 logic).
    """
    hist_vp = hist.iloc[-250:] if len(hist) >= 250 else hist
    p_min = float(hist_vp["Low"].min())
    p_max = float(hist_vp["High"].max())
    if p_max <= p_min:
        mid = (p_max + p_min) / 2
        return round(mid, 2), round(p_max, 2), round(p_min, 2)

    bins = np.linspace(p_min, p_max, n_bins + 1)
    vol_profile = np.zeros(n_bins)
    for _, row in hist_vp.iterrows():
        lo, hi, vol = float(row["Low"]), float(row["High"]), float(row["Volume"])
        day_range = hi - lo
        lo_idx = max(0, int(np.searchsorted(bins, lo, side="left")) - 1)
        hi_idx = min(n_bins - 1, int(np.searchsorted(bins, hi, side="right")) - 1)
        for j in range(lo_idx, hi_idx + 1):
            overlap = min(bins[j + 1], hi) - max(bins[j], lo)
            if overlap > 0:
                vol_profile[j] += vol * (overlap / day_range) if day_range > 0 else vol

    poc_idx = int(np.argmax(vol_profile))
    poc = round(float((bins[poc_idx] + bins[poc_idx + 1]) / 2), 2)

    total_vol = vol_profile.sum()
    order = np.argsort(vol_profile)[::-1]
    acc, va_bins = 0.0, []
    for idx in order:
        va_bins.append(int(idx))
        acc += vol_profile[idx]
        if acc >= 0.70 * total_vol:
            break
    vah = round(float(bins[max(va_bins) + 1]), 2)
    val = round(float(bins[min(va_bins)]), 2)
    return poc, vah, val


def _fetch_weekly_summary(hist) -> dict | None:
    """Compute weekly MA20/MA60 and slope flags from daily history."""
    try:
        w = hist["Close"].resample("W-FRI").last().dropna()
        if len(w) < 21:
            return None
        w_price = float(w.iloc[-1])
        w_ma20_raw = w.rolling(20).mean().iloc[-1]
        w_ma20 = round(float(w_ma20_raw), 2) if not math.isnan(w_ma20_raw) else None
        w_ma60_raw = w.rolling(60).mean().iloc[-1] if len(w) >= 60 else float("nan")
        w_ma60 = round(float(w_ma60_raw), 2) if not math.isnan(w_ma60_raw) else None
        w_ma20_slope = bool(w.iloc[-1] > w.iloc[-21]) if len(w) >= 21 else None
        w_ma60_slope = bool(w.iloc[-1] > w.iloc[-61]) if len(w) >= 61 else None
        if w_ma60 is not None:
            w_align = ("多头排列" if w_price > w_ma20 > w_ma60
                       else "空头排列" if w_price < w_ma20 < w_ma60
                       else "混合")
        else:
            w_align = "多头" if w_ma20 and w_price > w_ma20 else "空头" if w_ma20 else "N/A"
        return {
            "w_price":             round(w_price, 2),
            "w_ma20":              w_ma20,
            "w_ma60":              w_ma60,
            "w_ma20_slope_pos":    w_ma20_slope,
            "w_ma60_slope_pos":    w_ma60_slope,
            "w_above_ma20":        w_ma20 is not None and w_price > w_ma20,
            "w_alignment":         w_align,
        }
    except Exception as exc:
        log.debug("Weekly summary failed: %s", exc)
        return None


def fetch_stock_data(ticker: str) -> str:
    """Return a markdown snapshot of price, fundamentals, and technicals for one ticker."""
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="2y", interval="1d")

        if hist.empty or len(hist) < 20:
            return f"{ticker}: insufficient historical data from yfinance."

        # ── Price action ──────────────────────────────────────────────────────
        current    = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        chg        = current - prev_close
        chg_pct    = (chg / prev_close * 100) if prev_close else 0.0
        wk52_high  = float(hist["High"].max())
        wk52_low   = float(hist["Low"].min())
        vol_today  = float(hist["Volume"].iloc[-1])
        vol_avg20  = float(hist["Volume"].rolling(20).mean().iloc[-1])

        # ── Moving averages ───────────────────────────────────────────────────
        ma20  = float(hist["Close"].rolling(20).mean().iloc[-1])
        ma50  = float(hist["Close"].rolling(50).mean().iloc[-1])
        ma200_raw = hist["Close"].rolling(200).mean().iloc[-1]
        ma200 = float(ma200_raw) if not math.isnan(ma200_raw) else None

        ma60_raw  = hist["Close"].rolling(60).mean().iloc[-1]
        ma120_raw = hist["Close"].rolling(120).mean().iloc[-1]
        ma60  = float(ma60_raw)  if not math.isnan(ma60_raw)  else None
        ma120 = float(ma120_raw) if not math.isnan(ma120_raw) else None

        # ── EMA (Lei's system: EMA is the fast "trigger" line) ────────────────
        ema20  = round(float(hist["Close"].ewm(span=20,  adjust=False).mean().iloc[-1]), 2)
        ema60  = round(float(hist["Close"].ewm(span=60,  adjust=False).mean().iloc[-1]), 2)
        ema120 = round(float(hist["Close"].ewm(span=120, adjust=False).mean().iloc[-1]), 2)

        # ── 抵扣价斜率 (Lei's slope logic: price today vs. price N days ago) ──
        cs = hist["Close"]
        ma20_slope_pos  = bool(cs.iloc[-1] > cs.iloc[-21])  if len(cs) >= 21  else None
        ma60_slope_pos  = bool(cs.iloc[-1] > cs.iloc[-61])  if len(cs) >= 61  else None
        ma120_slope_pos = bool(cs.iloc[-1] > cs.iloc[-121]) if len(cs) >= 121 else None

        # ── 均线排列 & 密集区 ─────────────────────────────────────────────────
        _ma_set = [v for v in [ma20, ma60, ma120] if v is not None]
        bullish_align = len(_ma_set) == 3 and current > ma20 > ma60 > ma120
        bearish_align = len(_ma_set) == 3 and current < ma20 < ma60 < ma120
        alignment_str = ("多头排列 ✅" if bullish_align
                         else "空头排列 ⚠️" if bearish_align
                         else "混合/过渡")
        ma_spread_pct = ((max(_ma_set) - min(_ma_set)) / min(_ma_set) * 100
                         if len(_ma_set) >= 2 else None)
        ma_dense = ma_spread_pct is not None and ma_spread_pct < 2.0

        # ── 10日最低价 (stop-loss reference per Lei) ──────────────────────────
        low_10d = round(float(hist["Low"].iloc[-10:].min()), 2) if len(hist) >= 10 else None

        # ── 5日量比 (recent volume pattern) ──────────────────────────────────
        vol_5d_ratios: list[float] = []
        if vol_avg20 > 0 and len(hist) >= 5:
            vol_5d_ratios = [round(float(v) / vol_avg20, 2)
                             for v in hist["Volume"].iloc[-5:]]

        # ── 周线摘要 (weekly context) ─────────────────────────────────────────
        w_summary = _fetch_weekly_summary(hist)

        # ── Volume Profile / 筹码分布 ─────────────────────────────────────────
        poc, vah, val = _compute_volume_profile(hist)
        poc_vs_price = round((current / poc - 1) * 100, 1) if poc else None

        # ── Momentum / volatility via stockstats ──────────────────────────────
        hist_r = hist.reset_index()
        hist_r.columns = [c.lower() for c in hist_r.columns]
        ss      = ss_wrap(hist_r)
        rsi     = float(ss["rsi"].iloc[-1])
        atr     = float(ss["atr"].iloc[-1])
        macd    = float(ss["macd"].iloc[-1])
        macds   = float(ss["macds"].iloc[-1])
        boll_ub = float(ss["boll_ub"].iloc[-1])
        boll_lb = float(ss["boll_lb"].iloc[-1])

        # ── Trend context helpers ─────────────────────────────────────────────
        def _trend_vs_ma(price: float, ma: float | None, label: str) -> str:
            if ma is None:
                return f"{label}: N/A"
            diff_pct = (price - ma) / ma * 100
            arrow = "above" if price > ma else "below"
            return f"{arrow} {label} by {abs(diff_pct):.1f}%"

        trend_notes = (
            f"{_trend_vs_ma(current, ma50, 'MA50')} · "
            f"{_trend_vs_ma(current, ma200, 'MA200')}"
        )
        rsi_note = (
            "Overbought (>70)" if rsi > 70
            else "Oversold (<30)" if rsi < 30
            else "Neutral (30–70)"
        )
        macd_note = "Bullish (MACD > Signal)" if macd > macds else "Bearish (MACD < Signal)"

        # ── Fundamentals ──────────────────────────────────────────────────────
        company      = info.get("longName", ticker)
        sector       = info.get("sector", "N/A")
        industry     = info.get("industry", "N/A")
        mktcap       = info.get("marketCap")
        pe_trailing  = info.get("trailingPE")
        pe_forward   = info.get("forwardPE")
        eps_ttm      = info.get("trailingEps")
        revenue_ttm  = info.get("totalRevenue")
        rev_growth   = info.get("revenueGrowth")
        gross_margin = info.get("grossMargins")
        net_margin   = info.get("profitMargins")
        div_yield    = info.get("dividendYield")
        beta         = info.get("beta")
        analyst_mean = info.get("targetMeanPrice")
        analyst_low  = info.get("targetLowPrice")
        analyst_high = info.get("targetHighPrice")
        num_analysts = info.get("numberOfAnalystOpinions")
        short_pct    = info.get("shortPercentOfFloat")

        def _fmt_large(v) -> str:
            if v is None:
                return "N/A"
            if v >= 1e12:
                return f"${v/1e12:.2f}T"
            if v >= 1e9:
                return f"${v/1e9:.2f}B"
            if v >= 1e6:
                return f"${v/1e6:.2f}M"
            return f"${v:.2f}"

        def _fmt_pct(v) -> str:
            return "N/A" if v is None else f"{v*100:.1f}%"

        def _fmt_float(v, prefix="", suffix="", decimals=2) -> str:
            return "N/A" if v is None else f"{prefix}{v:.{decimals}f}{suffix}"

        # Analyst target upside/downside
        upside_str = "N/A"
        if analyst_mean and current:
            upside = (analyst_mean - current) / current * 100
            upside_str = f"${analyst_mean:.2f} ({upside:+.1f}%)"

        # Short interest flag
        short_str = _fmt_pct(short_pct)
        if short_pct and short_pct > 0.10:
            short_str += " ⚠️ High short interest"

        lines = [
            f"\n### {company} ({ticker})\n",

            "**Price & Market Action**\n",
            "| Metric                  | Value                   |",
            "|:------------------------|:------------------------|",
            f"| Current Price           | ${current:.2f}           |",
            f"| Day Change              | {chg:+.2f} ({chg_pct:+.2f}%)     |",
            f"| 52-Week High            | ${wk52_high:.2f}          |",
            f"| 52-Week Low             | ${wk52_low:.2f}           |",
            f"| Volume (today)          | {vol_today:,.0f}           |",
            f"| Avg Volume (20-day)     | {vol_avg20:,.0f}           |",
            "",

            "**Fundamentals**\n",
            "| Metric                  | Value                   |",
            "|:------------------------|:------------------------|",
            f"| Sector                  | {sector}               |",
            f"| Industry                | {industry}             |",
            f"| Market Cap              | {_fmt_large(mktcap)}   |",
            f"| Trailing P/E            | {_fmt_float(pe_trailing, decimals=1)} |",
            f"| Forward P/E             | {_fmt_float(pe_forward, decimals=1)} |",
            f"| EPS (TTM)               | {_fmt_float(eps_ttm, prefix='$')} |",
            f"| Revenue (TTM)           | {_fmt_large(revenue_ttm)} |",
            f"| Revenue Growth (YoY)    | {_fmt_pct(rev_growth)}  |",
            f"| Gross Margin            | {_fmt_pct(gross_margin)} |",
            f"| Net Margin              | {_fmt_pct(net_margin)}  |",
            f"| Dividend Yield          | {_fmt_pct(div_yield)}   |",
            f"| Beta                    | {_fmt_float(beta)}      |",
            f"| Analyst Target (mean)   | {upside_str}            |",
            f"| Analyst Count           | {num_analysts if num_analysts else 'N/A'} |",
            f"| Short Interest (% float)| {short_str}             |",
            "",

            "**Technical Levels**\n",
            "| Indicator               | Value                   | Signal               |",
            "|:------------------------|:------------------------|:---------------------|",
            f"| 20-Day MA               | ${ma20:.2f}              | {_trend_vs_ma(current, ma20, 'MA20')} |",
            f"| 50-Day MA               | ${ma50:.2f}              | {_trend_vs_ma(current, ma50, 'MA50')} |",
            f"| 60-Day MA               | {'${:.2f}'.format(ma60) if ma60 else 'N/A'} | {_trend_vs_ma(current, ma60, 'MA60')} |",
            f"| 120-Day MA              | {'${:.2f}'.format(ma120) if ma120 else 'N/A'} | {_trend_vs_ma(current, ma120, 'MA120')} |",
            f"| 200-Day MA              | {'${:.2f}'.format(ma200) if ma200 else 'N/A'} | {_trend_vs_ma(current, ma200, 'MA200')} |",
            f"| EMA20                   | ${ema20:.2f}              | {_trend_vs_ma(current, ema20, 'EMA20')} |",
            f"| EMA60                   | ${ema60:.2f}              | {_trend_vs_ma(current, ema60, 'EMA60')} |",
            f"| RSI(14)                 | {rsi:.1f}                 | {rsi_note}           |",
            f"| ATR(14)                 | ${atr:.2f}               | Daily volatility proxy |",
            f"| MACD                    | {macd:.4f}               | {macd_note}          |",
            f"| Bollinger Upper Band    | ${boll_ub:.2f}           |                      |",
            f"| Bollinger Lower Band    | ${boll_lb:.2f}           |                      |",
            "",
            f"**Trend Summary:** {trend_notes}",
            "",
            "**Lei Trading System — Entry Signals**\n",
            "| Indicator               | Value                   | Signal               |",
            "|:------------------------|:------------------------|:---------------------|",
            f"| 均线排列                | {alignment_str}         |                      |",
            f"| MA20 斜率 (抵扣价)      | {'今日>20日前 ✅' if ma20_slope_pos else '今日<20日前 ⚠️' if ma20_slope_pos is not None else 'N/A'} | {'MA20向上' if ma20_slope_pos else 'MA20向下'} |",
            f"| MA60 斜率 (抵扣价)      | {'今日>60日前 ✅' if ma60_slope_pos else '今日<60日前 ⚠️' if ma60_slope_pos is not None else 'N/A'} | {'MA60向上' if ma60_slope_pos else 'MA60向下'} |",
            f"| MA120 斜率 (抵扣价)     | {'今日>120日前 ✅' if ma120_slope_pos else '今日<120日前 ⚠️' if ma120_slope_pos is not None else 'N/A'} | {'MA120向上' if ma120_slope_pos else 'MA120向下'} |",
            f"| 密集区?                 | {'是 ⚠️ 趋势不明' if ma_dense else f'否 (间距{ma_spread_pct:.1f}%)'} |  |",
            f"| POC 筹码主峰            | ${poc:.2f}               | 价格{'站上' if poc_vs_price and poc_vs_price > 0 else '跌破'} POC {f'{poc_vs_price:+.1f}%' if poc_vs_price is not None else ''} |",
            f"| 价值区间 VAH/VAL        | ${vah:.2f} / ${val:.2f}  | 成交量70%集中区间    |",
            f"| 10日最低价 (止损参考)   | {'${:.2f}'.format(low_10d) if low_10d else 'N/A'} |                      |",
            f"| 近5日量比               | {', '.join(str(r) for r in vol_5d_ratios) if vol_5d_ratios else 'N/A'} | <0.7=缩量 >1.5=放量  |",
            "",
            "**周线状态 (Weekly Context)**\n",
            *(["| 指标 | 数值 | 信号 |",
               "|:-----|-----:|:-----|",
               f"| 周MA20 | ${w_summary['w_ma20']:.2f} | {'价格站上 ✅' if w_summary['w_above_ma20'] else '价格跌破 ⚠️'} |",
               f"| 周MA60 | {'${:.2f}'.format(w_summary['w_ma60']) if w_summary['w_ma60'] else 'N/A'} | |",
               f"| 周MA20斜率 | {'正 ✅' if w_summary['w_ma20_slope_pos'] else '负 ⚠️'} | 周线大势方向 |",
               f"| 周线排列 | {w_summary['w_alignment']} | |"]
             if w_summary else ["周线数据不足 (需更长历史)"]),
        ]
        return "\n".join(lines)

    except Exception as exc:
        log.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return f"{ticker}: could not fetch data — {exc}"


def fetch_yfinance_news(tickers: list[str], max_per_ticker: int = 8) -> list[dict]:
    """Fetch and normalise yfinance news for a list of stock tickers."""
    results:   list[dict] = []
    seen_urls: set[str]   = set()

    for ticker in tickers:
        try:
            news = yf.Ticker(ticker).news or []
            log.info("yfinance news for %s: %d items", ticker, len(news))
            for item in news[:max_per_ticker]:
                content = item.get("content", item)
                url = (content.get("canonicalUrl", {}).get("url", "")
                       or content.get("clickThroughUrl", {}).get("url", "")
                       or item.get("link", ""))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                pub_date = content.get("pubDate", "") or item.get("providerPublishTime", "")
                if isinstance(pub_date, (int, float)):
                    pub_date = datetime.utcfromtimestamp(pub_date).strftime("%Y-%m-%d")

                provider = (content.get("provider", {}).get("displayName", "")
                            or item.get("publisher", ""))
                summary  = (content.get("summary", "") or content.get("description", "")
                            or item.get("summary", ""))
                results.append({
                    "title":      content.get("title", item.get("title", "")),
                    "source":     provider,
                    "link":       url,
                    "date":       pub_date,
                    "snippet":    summary,
                    "_yf_ticker": ticker,
                })
        except Exception as exc:
            log.warning("yfinance news fetch failed for %s: %s", ticker, exc)

    log.info("yfinance news total: %d unique items", len(results))
    return results
