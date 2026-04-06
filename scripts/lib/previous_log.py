"""Load the previous day's log file for LLM baseline injection."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
_TAIL_CHARS = 4000


def load_previous_log(log_prefix: str = "daily_report") -> str | None:
    """Return the tail of the most recent log matching *log_prefix*, or None."""
    if not _LOGS_DIR.exists():
        return None

    candidates = sorted(
        _LOGS_DIR.glob(f"{log_prefix}_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Skip error logs
    candidates = [p for p in candidates if "error" not in p.name]

    if not candidates:
        log.info("No previous log found for prefix '%s'", log_prefix)
        return None

    path = candidates[0]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        excerpt = text[-_TAIL_CHARS:] if len(text) > _TAIL_CHARS else text
        log.info("Loaded previous log: %s (%d chars)", path.name, len(excerpt))
        return excerpt
    except Exception as exc:
        log.warning("Could not read previous log %s: %s", path, exc)
        return None
