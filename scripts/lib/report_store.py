"""Save and load clean analysis text for LLM baseline injection.

Reports (clean analysis) go to reports/ — used for day-over-day baseline.
Logs (infrastructure output) stay in logs/ — used for debugging only.
"""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


def save_report(prefix: str, text: str) -> Path:
    """Write analysis text to reports/<prefix>_YYYY-MM-DD.md and return the path."""
    _REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = _REPORTS_DIR / f"{prefix}_{date_str}.md"
    path.write_text(text, encoding="utf-8")
    log.info("Report saved: %s", path.name)
    return path


def _extract_synthesis(text: str) -> str:
    """Extract the STEP 3 Daily Synthesis section from a full report.

    Injecting only the synthesis (~600–1000 chars) instead of the full report
    (~20K chars) dramatically reduces prompt size while preserving the most
    actionable continuity context for the LLM.
    Falls back to the full text if the section cannot be located.
    """
    for marker in (
        "## STEP 3 — DAILY SYNTHESIS",
        "## STEP 3 - DAILY SYNTHESIS",
        "### 📊 Today's Macro Synthesis",
        "## STEP 3",
    ):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:].strip()
    log.warning("Synthesis section not found in previous report — injecting full text")
    return text.strip()


def load_previous_report(prefix: str) -> str | None:
    """Return the Daily Synthesis section of the most recent saved report, or None.

    Skips today's file so a re-run mid-day doesn't use its own output as baseline.
    Only the STEP 3 synthesis is returned (not the full analysis) to keep prompt
    size manageable while preserving day-over-day regime continuity.
    """
    if not _REPORTS_DIR.exists():
        return None

    today_stem = f"{prefix}_{datetime.now().strftime('%Y-%m-%d')}"
    candidates = sorted(
        (p for p in _REPORTS_DIR.glob(f"{prefix}_*.md") if p.stem != today_stem),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        log.info("No previous report found for prefix '%s'", prefix)
        return None

    path = candidates[0]
    try:
        full_text = path.read_text(encoding="utf-8")
        synthesis = _extract_synthesis(full_text)
        log.info(
            "Loaded previous report: %s (full: %d chars → synthesis: %d chars)",
            path.name, len(full_text), len(synthesis),
        )
        return synthesis
    except Exception as exc:
        log.warning("Could not read report %s: %s", path, exc)
        return None
