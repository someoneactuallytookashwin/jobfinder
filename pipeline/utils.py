"""Shared helpers: logging, hashing, JSON I/O, file/path helpers, sanitizing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import config

_LOGGER_NAME = "job_pipeline"
_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Return a singleton logger that writes to both console and pipeline.log.

    All LLM calls and pipeline steps log here so failures can be debugged after
    the fact (see config.LOG_PATH).
    """
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — full debug trail lives in pipeline.log (gitignored).
    file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    _logger = logger
    return logger


def hash_url(url: str) -> str:
    """Stable short id for a job, derived from its URL (used for dedupe)."""
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:12]


def now_iso() -> str:
    """Current local time as an ISO 8601 string."""
    return datetime.now().replace(microsecond=0).isoformat()


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def ensure_dirs() -> None:
    """Create the data/ and resume/output/ directories if they don't exist.

    These are gitignored but must exist at runtime.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)


def batch_dir(batch_n: int) -> str:
    """Path to resume/output/batch_N/, created if missing."""
    path = os.path.join(config.OUTPUT_DIR, f"batch_{batch_n}")
    os.makedirs(path, exist_ok=True)
    return path


def load_json(path: str, default: Any = None) -> Any:
    """Load JSON from path, returning `default` if the file is missing."""
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str, data: Any) -> None:
    """Write JSON to path (pretty-printed, utf-8)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def read_text(path: str) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path: str, text: str) -> None:
    """Write a UTF-8 text file, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def slugify(value: str, max_len: int = 40) -> str:
    """Turn arbitrary text into a safe filename fragment.

    e.g. "Acme Corp!" -> "acme_corp"
    """
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "_", value)
    value = value.strip("_")
    return value[:max_len] or "untitled"


def truncate(text: str, max_chars: int = 6000) -> str:
    """Clip long text so we don't blow the model's context window."""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " …[truncated]"
