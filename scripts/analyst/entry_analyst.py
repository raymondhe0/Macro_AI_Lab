#!/usr/bin/env python3
"""
Entry Signal Fetcher — computes Lei 价量时空 entry signals for any ticker.

Prints SECTION X (pre-computed checklist + R:R) to stdout.
Claude in the conversation then performs the 4-section analysis directly.

Usage:
  python3 entry_analyst.py --ticker TSM
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.entry_signals import fetch_single_lei_stock, format_entry_section

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lei Entry Signal Fetcher")
    parser.add_argument("--ticker", required=True, metavar="TICKER")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    log.info("Fetching Lei data for %s (%s)...", ticker, datetime.now().strftime("%Y-%m-%d"))

    s = fetch_single_lei_stock(ticker)
    if not s:
        log.error("Could not fetch data for %s", ticker)
        sys.exit(1)

    print(format_entry_section([s]))


if __name__ == "__main__":
    main()
