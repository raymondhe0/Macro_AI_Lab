"""Unified Serper search + article extraction for all pipelines."""

import logging
import requests
from datetime import date, timedelta

from .config import SERPER_API_KEY

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    import re as _re
    _HAS_TRAFILATURA = False

log = logging.getLogger(__name__)


# ── Date helpers ──────────────────────────────────────────────────────────────

def build_tbs(start: date | None, end: date | None) -> str | None:
    """Build Google tbs date-range string for Serper."""
    if start and end:
        return f"cdr:1,cd_min:{start.strftime('%-m/%-d/%Y')},cd_max:{end.strftime('%-m/%-d/%Y')}"
    if start:
        return f"cdr:1,cd_min:{start.strftime('%-m/%-d/%Y')},cd_max:{date.today().strftime('%-m/%-d/%Y')}"
    if end:
        return f"cdr:1,cd_min:1/1/2000,cd_max:{end.strftime('%-m/%-d/%Y')}"
    return None


def last_24h_tbs() -> str:
    """Return a tbs string covering the last 24 hours."""
    yesterday = date.today() - timedelta(days=1)
    return build_tbs(yesterday, date.today())


def last_nhours_tbs(hours: int) -> str:
    """Return a tbs string covering the last *hours* hours."""
    start = date.today() - timedelta(hours=hours)
    return build_tbs(start, date.today())


# ── Serper search ─────────────────────────────────────────────────────────────

def serper_search(query: str, num: int = 5, tbs: str | None = None) -> list[dict]:
    """Call Serper /news and return raw result list."""
    url     = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload: dict = {"q": query, "num": num, "gl": "us", "hl": "en"}
    if tbs:
        payload["tbs"] = tbs
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("news", [])


# ── Article extraction ────────────────────────────────────────────────────────

def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    """Fetch and extract clean article text. Uses trafilatura when available."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()

        if _HAS_TRAFILATURA:
            text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        else:
            text = _re.sub(r"<[^>]+>", " ", r.text)
            text = _re.sub(r"\s+", " ", text).strip()

        return text[:max_chars]
    except Exception as exc:
        log.warning("Could not fetch %s — %s", url, exc)
        return ""
