#!/usr/bin/env python3
"""
Macro AI Lab — Daily Macro Strategy Report
Fetches financial news via Serper.dev, reasons with local Qwen2.5 via Ollama,
translates to Chinese, and delivers a styled HTML email.

Usage:
  python3 macro_analyst.py           # full run
  python3 macro_analyst.py --test    # fast test: 1 query, no Ollama, sends [TEST] email
"""

import argparse
import os
import re
import smtplib
import logging
import requests
import markdown
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

SEARCH_QUERIES = [
    "site:cnbc.com US Treasury yield Fed interest rate",
    "site:reuters.com financial markets economy today",
    "site:marketwatch.com macroeconomic news today",
    "site:investing.com macroeconomic news markets today",
    "Fed Federal Reserve interest rate decision latest",
    "CPI inflation NFP jobs data latest 2026",
    "S&P 500 Nasdaq market outlook geopolitical risk",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Serper Search ─────────────────────────────────────────────────────────────

def serper_search(query: str, num: int = 5) -> list[dict]:
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num, "gl": "us", "hl": "en"}
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("news", [])


def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        log.warning("Could not fetch %s — %s", url, exc)
        return ""


def gather_news(test_mode: bool = False) -> list[dict]:
    queries = SEARCH_QUERIES[:1] if test_mode else SEARCH_QUERIES
    max_full = 0 if test_mode else 4

    seen_urls: set[str] = set()
    results: list[dict] = []
    full_fetched = 0

    for query in queries:
        log.info("Searching: %s", query)
        try:
            items = serper_search(query)
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            continue

        for item in items:
            url = item.get("link", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if full_fetched < max_full and url:
                log.info("  Fetching full article: %s", url)
                item["full_text"] = fetch_article_text(url)
                if item["full_text"]:
                    full_fetched += 1

            results.append(item)

    log.info("Collected %d unique news items (%d with full text)", len(results), full_fetched)
    return results


# ── Ollama Inference ──────────────────────────────────────────────────────────

def build_user_message(news_items: list[dict]) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"Today is {today}. Below are the latest financial news items retrieved:\n"]

    for i, item in enumerate(news_items, 1):
        lines.append(f"--- Article {i} ---")
        lines.append(f"Title   : {item.get('title', 'N/A')}")
        lines.append(f"Source  : {item.get('source', 'N/A')}")
        lines.append(f"URL     : {item.get('link', 'N/A')}")
        lines.append(f"Date    : {item.get('date', 'N/A')}")
        lines.append(f"Snippet : {item.get('snippet', 'N/A')}")
        if item.get("full_text"):
            lines.append(f"Body    : {item['full_text']}")
        lines.append("")

    lines.append(
        "Apply Noise Filtering first, then produce the Playbook analysis "
        "for every event that scores 7 or higher."
    )
    return "\n".join(lines)


def run_ollama(system: str, user: str, label: str = "") -> str:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
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
    seen = set()
    count = 0
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
    background: #f4f6f9;
    margin: 0; padding: 20px;
    color: #1a1a2e;
  }}
  .container {{
    max-width: 760px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  }}
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
    color: #ffffff;
    padding: 28px 32px;
  }}
  .header h1 {{ margin: 0 0 6px 0; font-size: 22px; letter-spacing: 1px; }}
  .header .meta {{ font-size: 13px; color: #a0aec0; }}
  .body {{ padding: 28px 32px; line-height: 1.8; }}
  h3 {{ color: #0f3460; border-left: 4px solid #e94560; padding-left: 12px; margin-top: 32px; }}
  strong {{ color: #16213e; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  th {{ background: #0f3460; color: #fff; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #e2e8f0; }}
  tr:nth-child(even) td {{ background: #f8fafc; }}
  hr {{ border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }}
  ul {{ padding-left: 20px; }}
  li {{ margin-bottom: 6px; }}
  a {{ color: #0f3460; text-decoration: none; border-bottom: 1px dotted #0f3460; }}
  a:hover {{ color: #e94560; border-bottom-color: #e94560; }}
  .footer {{
    background: #f8fafc;
    padding: 16px 32px;
    font-size: 12px;
    color: #718096;
    border-top: 1px solid #e2e8f0;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 Macro AI Lab · 每日宏观策略报告</h1>
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


def render_html(chinese_md: str, news_items: list[dict], model: str) -> str:
    sources_md = build_sources_md(news_items)
    full_md    = chinese_md + "\n" + sources_md
    body_html  = markdown.markdown(full_md, extensions=["tables", "fenced_code", "nl2br"])
    return HTML_TEMPLATE.format(
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
    parser = argparse.ArgumentParser(description="Macro AI Lab — Daily Report")
    parser.add_argument("--test", action="store_true",
                        help="Fast test: 1 query, skip Ollama, send [TEST] email")
    args = parser.parse_args()

    today_str      = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = "[TEST] " if args.test else ""

    log.info("=== Macro AI Lab — Daily Report %s%s ===",
             today_str, " (TEST MODE)" if args.test else "")

    # 1. Gather news
    news_items = gather_news(test_mode=args.test)
    if not news_items:
        log.warning("No news collected. Aborting.")
        return

    # 2. Analysis + Translation (skipped in test mode)
    if args.test:
        english = "[TEST MODE] Ollama inference skipped. Pipeline smoke-test only."
        chinese = "[测试模式] 已跳过 Ollama 推理。仅验证邮件发送流程。"
        log.info("Test mode — skipping Ollama inference")
    else:
        analysis_prompt    = PromptLoader.load("macro", "analysis")
        translation_prompt = PromptLoader.load("macro", "translation")
        user_msg           = build_user_message(news_items)
        english            = run_ollama(analysis_prompt, user_msg, label="analysis")
        log.info("English analysis complete (%d chars)", len(english))
        chinese            = run_ollama(translation_prompt, english, label="translation")
        log.info("Chinese translation complete (%d chars)", len(chinese))

    # 3. Build HTML and send
    html    = render_html(chinese, news_items, OLLAMA_MODEL)
    subject = f"{subject_prefix}【宏观AI实验室】每日策略报告 — {today_str}"

    print(english)

    try:
        send_email(subject, html, english)
    except Exception as exc:
        log.error("Email delivery failed: %s", exc)
        log.info("Report printed above; check SMTP credentials in .env")


if __name__ == "__main__":
    main()
