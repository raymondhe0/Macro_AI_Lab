#!/usr/bin/env python3
"""
Financial News Fetcher — CLI wrapper around lib.search.

Usage:
  python3 fetch_news.py "AAPL earnings"
  python3 fetch_news.py "Fed interest rate" --date 2026-03-28
  python3 fetch_news.py "S&P 500" --start 2026-03-01 --end 2026-03-31
  python3 fetch_news.py "inflation CPI" --start 2026-03-01 --end 2026-03-31 --num 10
"""

import argparse
import json
from datetime import datetime, date

from lib.search import serper_search, build_tbs


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
    parser.add_argument("query")
    parser.add_argument("--date",  help="Single date (YYYY-MM-DD)")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end",   help="End date (YYYY-MM-DD)")
    parser.add_argument("--num",   type=int, default=10)
    parser.add_argument("--json",  action="store_true")
    args = parser.parse_args()

    if args.date:
        start = end = parse_date(args.date)
    else:
        start = parse_date(args.start) if args.start else None
        end   = parse_date(args.end)   if args.end   else None

    if start and end and start > end:
        parser.error("--start must be before --end")

    tbs   = build_tbs(start, end)
    items = serper_search(args.query, num=args.num, tbs=tbs)

    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
    else:
        print_results(items, args.query, start, end)


if __name__ == "__main__":
    main()
