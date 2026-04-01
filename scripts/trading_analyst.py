#!/usr/bin/env python3
"""
Macro AI Lab — NQ & GC Trading Strategy Report
Fetches news + technical levels via yfinance, reasons with local Qwen2.5,
translates to Chinese, and delivers a styled HTML email.

Usage:
  python3 trading_analyst.py                        # intraday, full run
  python3 trading_analyst.py --mode weekly          # weekly report
  python3 trading_analyst.py --test                 # fast test, [TEST] email
  python3 trading_analyst.py --mode weekly --test   # weekly test
"""

import argparse
import os
import re
import smtplib
import logging
import requests
import markdown
import yfinance as yf
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv
from prompt_loader import PromptLoader

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

SERPER_API_KEY   = os.environ["SERPER_API_KEY"]
OLLAMA_HOST      = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
SMTP_HOST        = os.environ["SMTP_HOST"]
SMTP_PORT        = int(os.getenv("SMTP_PORT", 587))
SMTP_USER        = os.environ["SMTP_USER"]
SMTP_PASSWORD    = os.environ["SMTP_PASSWORD"]
REPORT_RECIPIENT = os.environ["REPORT_RECIPIENT"]

INTRADAY_QUERIES = [
    "Nasdaq futures NQ premarket outlook today",
    "Gold futures GC price outlook today",
    "site:cnbc.com Fed speakers inflation economic data today",
    "US real yields TIPS gold relationship today",
    "VIX volatility index level today",
    "economic calendar key events today US market",
]

WEEKLY_EXTRA_QUERIES = [
    "CFTC COT report gold futures net positioning latest",
    "CFTC COT report Nasdaq large speculator positioning latest",
    "site:investing.com economic calendar this week major events",
]

# Allowlist of trusted sources (case-insensitive substring match).
# Any news item whose source does not match at least one entry is dropped.
TRUSTED_SOURCES = {
    # Wire services
    "reuters", "associated press", " ap ", "dow jones", "mt newswires",
    # TV / web financial media
    "cnbc", "bloomberg", "marketwatch", "barron",
    # Major press
    "wall street journal", "wsj", "financial times", "ft.com",
    "new york times", "nytimes", "cnn", "washington post",
    # Market data / FX platforms
    "yahoo finance", "investing.com", "forex.com",
}


def is_trusted_source(source: str) -> bool:
    src = source.lower()
    return any(trusted in src for trusted in TRUSTED_SOURCES)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Technical Levels via yfinance ─────────────────────────────────────────────

def compute_pivot_points(high: float, low: float, close: float) -> dict:
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    r2 = pp + (high - low)
    s1 = 2 * pp - high
    s2 = pp - (high - low)
    return {"PP": pp, "R1": r1, "R2": r2, "S1": s1, "S2": s2}


def fetch_technical_levels(ticker: str, label: str) -> str:
    """Return a formatted string of technical levels for the given futures ticker."""
    try:
        t    = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")

        if hist.empty or len(hist) < 6:
            return f"{label} ({ticker}): insufficient data from yfinance."

        # Prior week OHLC (last completed week = last 5 trading days before today)
        prior_week = hist.iloc[-6:-1]
        pw_high  = prior_week["High"].max()
        pw_low   = prior_week["Low"].min()
        pw_close = hist.iloc[-2]["Close"]

        # Moving averages
        ma20  = hist["Close"].rolling(20).mean().iloc[-1]
        ma50  = hist["Close"].rolling(50).mean().iloc[-1]
        ma200 = hist["Close"].rolling(200).mean().iloc[-1]

        # Current price (last available)
        current = hist["Close"].iloc[-1]

        # Pivot points from prior week H/L/C
        pivots = compute_pivot_points(pw_high, pw_low, pw_close)

        lines = [
            f"\n### {label} ({ticker}) — Technical Levels (auto-fetched via yfinance)\n",
            f"| Level            | Price      |",
            f"|:-----------------|:-----------|",
            f"| Current Price    | {current:>10.2f} |",
            f"| Prior Week High  | {pw_high:>10.2f} |",
            f"| Prior Week Low   | {pw_low:>10.2f} |",
            f"| Prior Week Close | {pw_close:>10.2f} |",
            f"| Pivot Point (PP) | {pivots['PP']:>10.2f} |",
            f"| Resistance 1 (R1)| {pivots['R1']:>10.2f} |",
            f"| Resistance 2 (R2)| {pivots['R2']:>10.2f} |",
            f"| Support 1 (S1)   | {pivots['S1']:>10.2f} |",
            f"| Support 2 (S2)   | {pivots['S2']:>10.2f} |",
            f"| 20-Day MA        | {ma20:>10.2f} |",
            f"| 50-Day MA        | {ma50:>10.2f} |",
            f"| 200-Day MA       | {ma200:>10.2f} |",
        ]
        return "\n".join(lines)

    except Exception as exc:
        log.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return f"{label} ({ticker}): could not fetch technical levels — {exc}"


def get_all_technical_levels() -> str:
    log.info("Fetching technical levels via yfinance…")
    nq = fetch_technical_levels("NQ=F", "Nasdaq 100 Futures (NQ)")
    gc = fetch_technical_levels("GC=F", "Gold Futures (GC)")
    return nq + "\n\n" + gc


# ── yfinance News Feed ────────────────────────────────────────────────────────

def fetch_yfinance_news(tickers: list[str] = ("NQ=F", "GC=F"), max_per_ticker: int = 8) -> list[dict]:
    """Fetch ticker-specific news from yfinance and normalise to the same dict
    format used by Serper results: {title, source, link, date, snippet}."""
    results: list[dict] = []
    seen_urls: set[str] = set()

    for ticker in tickers:
        try:
            t    = yf.Ticker(ticker)
            news = t.news or []
            log.info("yfinance news for %s: %d items", ticker, len(news))

            for item in news[:max_per_ticker]:
                # yfinance returns a nested structure; content lives in 'content'
                content = item.get("content", item)   # handle both old & new yfinance schema
                url     = (
                    content.get("canonicalUrl", {}).get("url", "")
                    or content.get("clickThroughUrl", {}).get("url", "")
                    or item.get("link", "")
                )
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                pub_date = content.get("pubDate", "") or item.get("providerPublishTime", "")
                if isinstance(pub_date, (int, float)):
                    pub_date = datetime.utcfromtimestamp(pub_date).strftime("%Y-%m-%d")

                provider = (
                    content.get("provider", {}).get("displayName", "")
                    or item.get("publisher", "")
                )
                summary = (
                    content.get("summary", "")
                    or content.get("description", "")
                    or item.get("summary", "")
                )

                results.append({
                    "title":   content.get("title", item.get("title", "")),
                    "source":  provider,
                    "link":    url,
                    "date":    pub_date,
                    "snippet": summary,
                    "_yf_ticker": ticker,   # tag so we know the origin
                })

        except Exception as exc:
            log.warning("yfinance news fetch failed for %s: %s", ticker, exc)

    log.info("yfinance news total: %d unique items", len(results))
    return results


# ── Serper Search ─────────────────────────────────────────────────────────────

def serper_search(query: str, num: int = 5) -> list[dict]:
    url     = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num, "gl": "us", "hl": "en"}
    resp    = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("news", [])


def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    try:
        r    = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        log.warning("Could not fetch %s — %s", url, exc)
        return ""


def gather_news(mode: str, test_mode: bool) -> list[dict]:
    queries   = INTRADAY_QUERIES[:1] if test_mode else INTRADAY_QUERIES
    max_full  = 0 if test_mode else 4

    if mode == "weekly" and not test_mode:
        queries = queries + WEEKLY_EXTRA_QUERIES

    # Seed with yfinance ticker-specific news (free, no API key), filter by source
    yf_news_raw = [] if test_mode else fetch_yfinance_news()
    yf_news     = [n for n in yf_news_raw if is_trusted_source(n.get("source", ""))]
    dropped_yf  = len(yf_news_raw) - len(yf_news)
    if dropped_yf:
        log.info("Source filter dropped %d yfinance items (untrusted sources)", dropped_yf)

    seen_urls: set[str] = set(item["link"] for item in yf_news)
    results:   list[dict] = list(yf_news)
    full_fetched = 0
    dropped_serper = 0

    for query in queries:
        log.info("Searching: %s", query)
        try:
            items = serper_search(query)
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            continue

        for item in items:
            url    = item.get("link", "")
            source = item.get("source", "")

            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not is_trusted_source(source):
                dropped_serper += 1
                log.debug("Dropped (untrusted): [%s] %s", source, item.get("title", ""))
                continue

            if full_fetched < max_full and url:
                log.info("  Fetching full article: %s", url)
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1

            results.append(item)

    if dropped_serper:
        log.info("Source filter dropped %d Serper items (untrusted sources)", dropped_serper)
    log.info("Collected %d trusted news items (%d with full text)", len(results), full_fetched)
    return results


# ── Ollama Inference ──────────────────────────────────────────────────────────

def build_user_message(news_items: list[dict], tech_levels: str, mode: str) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [
        f"Today is {today}. Mode: {'WEEKLY strategy' if mode == 'weekly' else 'INTRADAY strategy'}.\n",
        "=" * 60,
        "SECTION A — TECHNICAL LEVELS (from yfinance, use these exact prices in your setups)",
        "=" * 60,
        tech_levels,
        "",
        "=" * 60,
        "SECTION B — TODAY'S NEWS & MARKET CONTEXT",
        "=" * 60,
    ]

    for i, item in enumerate(news_items, 1):
        lines.append(f"\n--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")

    prompt_name = "weekly_nq_gc" if mode == "weekly" else "intraday_nq_gc"
    lines.append(
        f"\nUsing the technical levels from Section A and news from Section B, "
        f"produce the {'weekly' if mode == 'weekly' else 'intraday'} strategy report."
    )
    return "\n".join(lines)


def run_ollama(system: str, user: str, label: str = "") -> str:
    url     = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model":   OLLAMA_MODEL,
        "stream":  False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    log.info("Ollama call: %s (%s)…", label or "inference", OLLAMA_MODEL)
    resp = requests.post(url, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


# ── HTML Email ────────────────────────────────────────────────────────────────

def build_sources_md(news_items: list[dict]) -> str:
    lines = ["\n---\n### 参考来源 · Sources\n"]
    seen, count = set(), 0
    for item in news_items:
        url   = item.get("link", "")
        title = item.get("title", url)
        src   = item.get("source", "")
        date  = item.get("date", "")
        if not url or url in seen:
            continue
        seen.add(url)
        meta = " · ".join(filter(None, [src, date]))
        lines.append(f"- [{title}]({url}){' — ' + meta if meta else ''}")
        count += 1
        if count >= 15:
            break
    return "\n".join(lines)


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f4f6f9; margin: 0; padding: 20px; color: #1a1a2e;
  }}
  .container {{
    max-width: 760px; margin: 0 auto; background: #ffffff;
    border-radius: 10px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  }}
  .header {{
    background: linear-gradient(135deg, #0d1b2a 0%, #1b4332 60%, #1a6b3c 100%);
    color: #ffffff; padding: 28px 32px;
  }}
  .header h1 {{ margin: 0 0 6px 0; font-size: 22px; letter-spacing: 1px; }}
  .header .meta {{ font-size: 13px; color: #a0aec0; }}
  .body {{ padding: 28px 32px; line-height: 1.8; }}
  h3 {{ color: #1b4332; border-left: 4px solid #f6c90e; padding-left: 12px; margin-top: 32px; }}
  strong {{ color: #0d1b2a; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  th {{ background: #1b4332; color: #fff; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #e2e8f0; }}
  tr:nth-child(even) td {{ background: #f8fafc; }}
  hr {{ border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }}
  ul {{ padding-left: 20px; }} li {{ margin-bottom: 6px; }}
  a {{ color: #1b4332; text-decoration: none; border-bottom: 1px dotted #1b4332; }}
  a:hover {{ color: #f6c90e; border-bottom-color: #f6c90e; }}
  .footer {{
    background: #f8fafc; padding: 16px 32px; font-size: 12px;
    color: #718096; border-top: 1px solid #e2e8f0; text-align: center;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📈 Macro AI Lab · {report_type}</h1>
    <div class="meta">{date} &nbsp;·&nbsp; 模型: {model}</div>
  </div>
  <div class="body">{body}</div>
  <div class="footer">
    Macro AI Lab · 由本地 {model} 生成 · 仅供学习参考，不构成投资建议
  </div>
</div>
</body>
</html>
"""


def render_html(chinese_md: str, news_items: list[dict], model: str, report_type: str) -> str:
    sources_md = build_sources_md(news_items)
    full_md    = chinese_md + "\n" + sources_md
    body_html  = markdown.markdown(full_md, extensions=["tables", "fenced_code", "nl2br"])
    return HTML_TEMPLATE.format(
        report_type=report_type,
        date=datetime.now().strftime("%Y年%m月%d日  %H:%M"),
        model=model,
        body=body_html,
    )


def send_email(subject: str, html: str, plain_text: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = REPORT_RECIPIENT
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html,       "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, REPORT_RECIPIENT, msg.as_string())

    log.info("Report sent to %s", REPORT_RECIPIENT)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Macro AI Lab — Trading Report")
    parser.add_argument("--mode", choices=["intraday", "weekly"], default="intraday")
    parser.add_argument("--test", action="store_true",
                        help="Fast test: 1 query, skip Ollama, send [TEST] email")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    if args.mode == "weekly":
        report_type_en = "NQ & GC 周度策略报告"
        subject_cn     = f"{subject_prefix}【宏观AI实验室】NQ & GC 周度策略 — Week of {today_str}"
        prompt_name    = "weekly_nq_gc"
    else:
        report_type_en = "NQ & GC 日内策略报告"
        subject_cn     = f"{subject_prefix}【宏观AI实验室】NQ & GC 日内策略 — {today_str}"
        prompt_name    = "intraday_nq_gc"

    log.info("=== Macro AI Lab — %s %s%s ===",
             prompt_name, today_str, " (TEST MODE)" if args.test else "")

    # 1. Technical levels
    tech_levels = get_all_technical_levels()

    # 2. News
    news_items = gather_news(mode=args.mode, test_mode=args.test)
    if not news_items:
        log.warning("No news collected. Aborting.")
        return

    # 3. Analysis + Translation (skipped in test mode)
    if args.test:
        english = (
            f"[TEST MODE — {prompt_name}] Ollama inference skipped.\n\n"
            f"Technical levels fetched successfully:\n{tech_levels}"
        )
        chinese = (
            f"[测试模式 — {prompt_name}] 已跳过 Ollama 推理。\n\n"
            f"技术位已成功获取：\n{tech_levels}"
        )
        log.info("Test mode — skipping Ollama inference")
    else:
        analysis_prompt    = PromptLoader.load("trading", prompt_name)
        translation_prompt = PromptLoader.load("trading", "translation")
        user_msg           = build_user_message(news_items, tech_levels, args.mode)
        english            = run_ollama(analysis_prompt, user_msg, label=prompt_name)
        log.info("Analysis complete (%d chars)", len(english))
        chinese            = run_ollama(translation_prompt, english, label="translation")
        log.info("Translation complete (%d chars)", len(chinese))

    # 4. Build HTML and send
    html = render_html(chinese, news_items, OLLAMA_MODEL, report_type_en)
    print(english)

    try:
        send_email(subject_cn, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)


if __name__ == "__main__":
    main()
