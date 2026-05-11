"""Save and load raw pre-LLM data as JSON for cross-pipeline sharing.

raw_data/<prefix>_YYYY-MM-DD.json  — written by data_fetcher/, read by analyst/
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_RAW_DATA_DIR = Path(__file__).parent.parent.parent / "raw_data"


def save_raw(prefix: str, data: dict) -> Path:
    _RAW_DATA_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = _RAW_DATA_DIR / f"{prefix}_{date_str}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Raw data saved: %s (%d news items)", path.name, len(data.get("news_items", [])))
    return path


def load_raw(prefix: str) -> dict | None:
    if not _RAW_DATA_DIR.exists():
        return None
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = _RAW_DATA_DIR / f"{prefix}_{date_str}.json"
    if not path.exists():
        log.warning("No raw data for '%s' today — run the fetcher first", prefix)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        log.info("Raw data loaded: %s (%d news items)", path.name, len(data.get("news_items", [])))
        return data
    except Exception as exc:
        log.warning("Could not read %s: %s", path, exc)
        return None


def load_all_raw_today() -> dict[str, dict]:
    """Load every raw_data file saved today, keyed by prefix."""
    if not _RAW_DATA_DIR.exists():
        return {}
    date_str = datetime.now().strftime("%Y-%m-%d")
    result: dict[str, dict] = {}
    for path in sorted(_RAW_DATA_DIR.glob(f"*_{date_str}.json")):
        prefix = path.stem.replace(f"_{date_str}", "")
        try:
            result[prefix] = json.loads(path.read_text(encoding="utf-8"))
            log.info("Loaded raw: %s", path.name)
        except Exception as exc:
            log.warning("Could not read %s: %s", path, exc)
    if not result:
        log.warning("No raw_data files found for today — run data fetchers first")
    return result
