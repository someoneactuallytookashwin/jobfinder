"""Single wrapper for all Ollama LLM calls.

Everything that talks to the local LLM goes through here so that retries,
timeouts, logging, and JSON-parsing fallback live in exactly one place
(per the plan's "Notes for Claude Code").

This module assumes an Ollama server is already running locally at
config.OLLAMA_BASE_URL. It does NOT pull models or start the server —
you pull the models yourself (see the README).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import config
from pipeline.utils import get_logger

logger = get_logger()

# Lazy import so the rest of the pipeline (and `--help`) works even if the
# `ollama` package isn't installed yet.
_client = None


def _get_client():
    """Return a singleton ollama.Client pointed at config.OLLAMA_BASE_URL."""
    global _client
    if _client is None:
        try:
            from ollama import Client
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "The 'ollama' package is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc
        _client = Client(host=config.OLLAMA_BASE_URL, timeout=config.LLM_TIMEOUT)
    return _client


class LLMError(RuntimeError):
    """Raised when an LLM call fails after all retries."""


def call_llm(model: str, prompt: str, *, fmt: str | None = None) -> str:
    """Send a single prompt to Ollama and return the raw text response.

    Retries up to config.LLM_MAX_RETRIES times with backoff on any error
    (connection refused, timeout, server error). Every attempt is logged.

    Args:
        model: Ollama model tag, e.g. "qwen2.5:3b".
        prompt: The fully-rendered prompt string.
        fmt: Pass "json" to ask Ollama to constrain output to valid JSON.

    Raises:
        LLMError: if all attempts fail.
    """
    client = _get_client()
    last_err: Exception | None = None

    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        try:
            logger.info(
                "LLM call → model=%s attempt=%d/%d prompt_chars=%d fmt=%s",
                model, attempt, config.LLM_MAX_RETRIES, len(prompt), fmt,
            )
            kwargs: dict[str, Any] = {"model": model, "prompt": prompt}
            if fmt:
                kwargs["format"] = fmt
            response = client.generate(**kwargs)
            text = (response.get("response") or "").strip()
            logger.info(
                "LLM ok ← model=%s attempt=%d response_chars=%d",
                model, attempt, len(text),
            )
            if not text:
                raise LLMError("Empty response from model")
            return text
        except Exception as exc:  # noqa: BLE001 - we want to retry on anything
            last_err = exc
            logger.warning(
                "LLM call failed (model=%s attempt=%d/%d): %s",
                model, attempt, config.LLM_MAX_RETRIES, exc,
            )
            if attempt < config.LLM_MAX_RETRIES:
                backoff = 2 ** (attempt - 1)  # 1s, 2s, 4s, ...
                time.sleep(backoff)

    raise LLMError(
        f"LLM call to model '{model}' failed after "
        f"{config.LLM_MAX_RETRIES} attempts. Last error: {last_err}. "
        f"Is the Ollama server running at {config.OLLAMA_BASE_URL}?"
    )


def extract_json(text: str) -> dict | None:
    """Best-effort parse of a JSON object out of an LLM response.

    Tries, in order:
      1. Straight json.loads of the whole string.
      2. Stripping ```json fences.
      3. Grabbing the first {...} block via regex.

    Returns the parsed dict, or None if nothing parses.
    """
    if not text:
        return None

    # 1. Direct parse.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 2. Strip code fences (```json ... ``` or ``` ... ```).
    fenced = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE)
    fenced = re.sub(r"```$", "", fenced.strip())
    try:
        parsed = json.loads(fenced.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 3. First balanced-looking {...} block.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse JSON from LLM response: %.200s", text)
    return None


def call_llm_json(model: str, prompt: str, *, fallback: dict | None = None) -> dict:
    """Call the LLM expecting JSON, with a parsing fallback.

    Uses Ollama's format="json" mode, then runs the response through
    extract_json. If parsing still fails, returns `fallback` (or {}).
    """
    text = call_llm(model, prompt, fmt="json")
    parsed = extract_json(text)
    if parsed is None:
        logger.warning("Falling back to default for model=%s (unparseable JSON).", model)
        return dict(fallback) if fallback else {}
    return parsed
