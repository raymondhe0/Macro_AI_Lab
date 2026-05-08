"""Individual stock price snapshot, fundamentals, and technical levels via yfinance."""

import logging
import math
from datetime import datetime

import yfinance as yf
from stockstats import wrap as ss_wrap

log = logging.getLogger(__name__)


def fetch_stock_data(ticker: str) -> str:
    """Return a markdown snapshot of price, fundamentals, and technicals for one ticker."""
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="1y", interval="1d")

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
            f"| 200-Day MA              | {'${:.2f}'.format(ma200) if ma200 else 'N/A'} | {_trend_vs_ma(current, ma200, 'MA200')} |",
            f"| RSI(14)                 | {rsi:.1f}                 | {rsi_note}           |",
            f"| ATR(14)                 | ${atr:.2f}               | Daily volatility proxy |",
            f"| MACD                    | {macd:.4f}               | {macd_note}          |",
            f"| Bollinger Upper Band    | ${boll_ub:.2f}           |                      |",
            f"| Bollinger Lower Band    | ${boll_lb:.2f}           |                      |",
            "",
            f"**Trend Summary:** {trend_notes}",
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
