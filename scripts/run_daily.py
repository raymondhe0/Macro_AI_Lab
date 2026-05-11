#!/usr/bin/env python3
"""
Daily orchestrator for Macro AI Lab.

Weekday schedule (run via cron at 7:30 AM):
  1. fetch_macro + fetch_tech + fetch_stock   (parallel)
  2. macro_analyst + tech_analyst             (parallel — one LLM call each)
  3. portfolio_analyst                        (reads all raw data, runs last)

stock_analyst is demand-only — run manually:
  python3 analyst/stock_analyst.py --ticker NVDA GOOGL MSFT TSM META AMD AMZN

Usage:
  python3 run_daily.py        # normal run
  python3 run_daily.py --test # skip LLM calls, verify pipeline wiring
"""

import argparse
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent


def _load_stock_tickers() -> list[str]:
    """Load all tickers (core + watchlist) from watchlist.yaml."""
    path = Path(__file__).parent.parent / "config" / "watchlist.yaml"
    try:
        wl = yaml.safe_load(path.read_text(encoding="utf-8"))
        return [s["ticker"] for s in wl.get("core", []) + wl.get("watchlist", [])]
    except Exception as exc:
        log.warning("Could not load watchlist.yaml (%s) — falling back to defaults", exc)
        return ["NVDA", "GOOGL", "MSFT", "TSM"]


STOCK_TICKERS = _load_stock_tickers()


def run(cmd: list[str], label: str) -> bool:
    """Run a subprocess, stream output, return True on success."""
    log.info("▶ Starting: %s", label)
    t0 = time.time()
    result = subprocess.run(
        [sys.executable] + cmd,
        cwd=SCRIPTS_DIR,
        capture_output=False,
    )
    elapsed = time.time() - t0
    if result.returncode == 0:
        log.info("✓ Done: %s (%.0fs)", label, elapsed)
        return True
    else:
        log.error("✗ Failed: %s (exit %d)", label, result.returncode)
        return False


def run_parallel(tasks: list[tuple[list[str], str]]) -> dict[str, bool]:
    """Run multiple subprocesses in parallel, wait for all, return results."""
    results: dict[str, bool] = {}
    threads = []

    def _run(cmd, label):
        results[label] = run(cmd, label)

    for cmd, label in tasks:
        t = threading.Thread(target=_run, args=(cmd, label))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro AI Lab daily orchestrator")
    parser.add_argument("--test", action="store_true", help="Pass --test to all scripts")
    args = parser.parse_args()

    today = datetime.now()
    test_flag = ["--test"] if args.test else []

    log.info("=" * 60)
    log.info("Macro AI Lab — Daily Run  %s", today.strftime("%A, %B %d, %Y"))
    log.info("=" * 60)

    # ── Step 1: Fetch macro + tech + stock in parallel ────────────
    log.info("\n[Step 1] Fetching raw data (parallel): macro, tech, stock (%s)...",
             " ".join(STOCK_TICKERS))
    fetch_results = run_parallel([
        (["data_fetcher/fetch_macro.py"] + test_flag, "fetch_macro"),
        (["data_fetcher/fetch_tech.py"]  + test_flag, "fetch_tech"),
        (["data_fetcher/fetch_stock.py", "--ticker"] + STOCK_TICKERS + test_flag, "fetch_stock"),
    ])

    if not any(fetch_results.values()):
        log.error("All fetchers failed — aborting.")
        sys.exit(1)

    # ── Step 2: Run analysts (macro + tech in parallel) ───────────
    log.info("\n[Step 2] Running analysts (macro + tech in parallel)...")

    analyst_tasks = []
    for script, fetcher_key in [
        ("analyst/macro_analyst.py", "fetch_macro"),
        ("analyst/tech_analyst.py",  "fetch_tech"),
    ]:
        if not fetch_results.get(fetcher_key, True):
            log.warning("Skipping %s — its fetcher failed.", Path(script).stem)
        else:
            analyst_tasks.append(([script] + test_flag, Path(script).stem))

    analyst_results = run_parallel(analyst_tasks) if analyst_tasks else {}

    failed = [name for name, ok in analyst_results.items() if not ok]
    if failed:
        log.warning("Analyst(s) failed: %s — portfolio will run with partial data", failed)

    # ── Step 3: Portfolio synthesis (always last) ─────────────────
    log.info("\n[Step 3] Running portfolio analyst (synthesises all raw data)...")
    run(["analyst/portfolio_analyst.py"] + test_flag, "portfolio_analyst")

    log.info("\n" + "=" * 60)
    log.info("Daily run complete — %s", today.strftime("%H:%M:%S"))
    log.info("=" * 60)


if __name__ == "__main__":
    main()
