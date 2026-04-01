#!/usr/bin/env python3
"""
Financial News Fetcher — fetch news by ticker/topic with optional date filtering.

Usage:
  python3 fetch_news.py "AAPL earnings"
  python3 fetch_news.py "Fed interest rate" --date 2026-03-28
  python3 fetch_news.py "S&P 500" --start 2026-03-01 --end 2026-03-31
  python3 fetch_news.py "inflation CPI" --start 2026-03-01 --end 2026-03-31 --num 10
"""

import argparse
import os
import json
from datetime import datetime, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SERPER_API_KEY = os.environ["SERPER_API_KEY"]


def build_tbs(start: date | None, end: date | None) -> str | None:
    """Build Google tbs date-range string for Serper."""
    if start and end:
        return f"cdr:1,cd_min:{start.strftime('%-m/%-d/%Y')},cd_max:{end.strftime('%-m/%-d/%Y')}"
    if start:
        return f"cdr:1,cd_min:{start.strftime('%-m/%-d/%Y')},cd_max:{date.today().strftime('%-m/%-d/%Y')}"
    if end:
        return f"cdr:1,cd_min:1/1/2000,cd_max:{end.strftime('%-m/%-d/%Y')}"
    return None


def fetch_news(
    query: str,
    num: int = 10,
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    url = "https://google.serper.dev/news"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload: dict = {"q": query, "num": num, "gl": "us", "hl": "en"}

    tbs = build_tbs(start, end)
    if tbs:
        payload["tbs"] = tbs

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("news", [])


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def print_results(items: list[dict], query: str, start: date | None, end: date | None) -> None:
    date_range = ""
    if start and end:
        date_range = f" [{start} → {end}]"
    elif start:
        date_range = f" [from {start}]"
    elif end:
        date_range = f" [until {end}]"

    print(f'\n=== News: "{query}"{date_range} ({len(items)} results) ===\n')

    for i, item in enumerate(items, 1):
        print(f"[{i}] {item.get('title', 'N/A')}")
        print(f"     Source : {item.get('source', 'N/A')}")
        print(f"     Date   : {item.get('date', 'N/A')}")
        print(f"     URL    : {item.get('link', 'N/A')}")
        snippet = item.get("snippet", "")
        if snippet:
            print(f"     Snippet: {snippet[:120]}{'…' if len(snippet) > 120 else ''}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch financial news with optional date filter")
    parser.add_argument("query", help="Search query, e.g. 'AAPL earnings' or 'Fed rate decision'")
    parser.add_argument("--date",  help="Single date (YYYY-MM-DD) — sets both start and end")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end",   help="End date (YYYY-MM-DD)")
    parser.add_argument("--num",   type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--json",  action="store_true", help="Output raw JSON instead of formatted text")
    args = parser.parse_args()

    # Resolve dates
    if args.date:
        start = end = parse_date(args.date)
    else:
        start = parse_date(args.start) if args.start else None
        end   = parse_date(args.end)   if args.end   else None

    if start and end and start > end:
        parser.error("--start must be before --end")

    items = fetch_news(args.query, num=args.num, start=start, end=end)

    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
    else:
        print_results(items, args.query, start, end)


if __name__ == "__main__":
    main()
