"""Shared HTML email rendering and delivery."""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

from .config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_RECIPIENT

log = logging.getLogger(__name__)

# ── Source list ───────────────────────────────────────────────────────────────

def build_sources_md(news_items: list[dict], max_sources: int = 15) -> str:
    lines = ["\n---\n### 参考来源 · Sources\n"]
    seen, count = set(), 0
    for item in news_items:
        url   = item.get("link", "")
        title = item.get("title", url)
        src   = item.get("source", "")
        dt    = item.get("date", "")
        if not url or url in seen:
            continue
        seen.add(url)
        meta = " · ".join(filter(None, [src, dt]))
        lines.append(f"- [{title}]({url}){' — ' + meta if meta else ''}")
        count += 1
        if count >= max_sources:
            break
    return "\n".join(lines)


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
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
  .header {{ background: {header_gradient}; color: #ffffff; padding: 28px 32px; }}
  .header h1 {{ margin: 0 0 6px 0; font-size: 22px; letter-spacing: 1px; }}
  .header .meta {{ font-size: 13px; color: #a0aec0; }}
  .body {{ padding: 28px 32px; line-height: 1.8; }}
  h3 {{ color: {accent_color}; border-left: 4px solid {highlight_color}; padding-left: 12px; margin-top: 32px; }}
  strong {{ color: #16213e; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  th {{ background: {accent_color}; color: #fff; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #e2e8f0; }}
  tr:nth-child(even) td {{ background: #f8fafc; }}
  hr {{ border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }}
  ul {{ padding-left: 20px; }} li {{ margin-bottom: 6px; }}
  a {{ color: {accent_color}; text-decoration: none; border-bottom: 1px dotted {accent_color}; }}
  a:hover {{ color: {highlight_color}; border-bottom-color: {highlight_color}; }}
  .footer {{
    background: #f8fafc; padding: 16px 32px; font-size: 12px;
    color: #718096; border-top: 1px solid #e2e8f0; text-align: center;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title_emoji} Macro AI Lab · {title_text}</h1>
    <div class="meta">{date} &nbsp;·&nbsp; 模型: {model}</div>
  </div>
  <div class="body">{body}</div>
  <div class="footer">Macro AI Lab · 由 {model} 生成 · 仅供学习参考，不构成投资建议</div>
</div>
</body>
</html>
"""

# Preset styles: "macro" (dark blue) or "trading" (dark green)
_STYLES = {
    "macro": {
        "header_gradient": "linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%)",
        "accent_color":    "#0f3460",
        "highlight_color": "#e94560",
    },
    "trading": {
        "header_gradient": "linear-gradient(135deg, #0d1b2a 0%, #1b4332 60%, #1a6b3c 100%)",
        "accent_color":    "#1b4332",
        "highlight_color": "#f6c90e",
    },
}


def render_html(
    body_md: str,
    news_items: list[dict],
    model: str,
    title_emoji: str,
    title_text: str,
    style: str = "macro",
) -> str:
    sources_md = build_sources_md(news_items)
    full_md    = body_md + "\n" + sources_md
    body_html  = markdown.markdown(full_md, extensions=["tables", "fenced_code", "nl2br"])
    s = _STYLES.get(style, _STYLES["macro"])
    return _HTML_TEMPLATE.format(
        date=datetime.now().strftime("%Y年%m月%d日  %H:%M"),
        model=model,
        title_emoji=title_emoji,
        title_text=title_text,
        body=body_html,
        **s,
    )


# ── Delivery ──────────────────────────────────────────────────────────────────

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
