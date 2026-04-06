"""Centralised environment configuration for all Macro AI Lab pipelines."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── Serper ────────────────────────────────────────────────────────────────────
SERPER_API_KEY = os.environ["SERPER_API_KEY"]

# ── SMTP ──────────────────────────────────────────────────────────────────────
SMTP_HOST        = os.environ["SMTP_HOST"]
SMTP_PORT        = int(os.getenv("SMTP_PORT", 587))
SMTP_USER        = os.environ["SMTP_USER"]
SMTP_PASSWORD    = os.environ["SMTP_PASSWORD"]
REPORT_RECIPIENT = os.environ["REPORT_RECIPIENT"]

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_ENGINE        = os.getenv("LLM_ENGINE", "claude")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
OLLAMA_HOST       = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

ACTIVE_MODEL = CLAUDE_MODEL if LLM_ENGINE == "claude" else OLLAMA_MODEL

# ── Finnhub ───────────────────────────────────────────────────────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
