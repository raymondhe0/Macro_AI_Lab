"""LLM inference helpers shared across all pipelines."""

import logging

import anthropic
import requests

from .config import (
    LLM_ENGINE, ANTHROPIC_API_KEY, CLAUDE_MODEL,
    OLLAMA_HOST, OLLAMA_MODEL,
)

log = logging.getLogger(__name__)


def run_claude(system: str, user: str, label: str = "") -> str:
    log.info("Claude API call: %s (%s)…", label or "inference", CLAUDE_MODEL)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    return "".join(block.text for block in msg.content if block.type == "text")


def run_ollama(system: str, user: str, label: str = "") -> str:
    log.info("Ollama call: %s (%s)…", label or "inference", OLLAMA_MODEL)
    payload = {
        "model":   OLLAMA_MODEL,
        "stream":  False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def run_llm(system: str, user: str, label: str = "") -> str:
    if LLM_ENGINE == "claude":
        return run_claude(system, user, label)
    return run_ollama(system, user, label)
