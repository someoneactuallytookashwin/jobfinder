"""LLM fit scoring.

Pre-filters jobs by keyword (no LLM), then sends the top TOP_N_TO_RANK to the
scoring model, asks for structured JSON, and ranks everything by score.
"""

from __future__ import annotations

import os

import config
from pipeline import llm
from pipeline.utils import get_logger, read_text, truncate

logger = get_logger()

_SCORING_PROMPT_PATH = os.path.join("prompts", "scoring_prompt.txt")


def prefilter(jobs: list[dict]) -> list[dict]:
    """Keyword pre-filter before the LLM sees anything.

    - Keep jobs whose title OR description matches any SEARCH_KEYWORDS.
    - Drop jobs whose title contains any EXCLUDE_KEYWORDS.

    The include match looks at title + description because some sources (notably
    the YC/HN thread) have messy free-text titles — the actual role lives in the
    posting body, so a title-only match would wrongly drop them. The exclude
    match stays title-only, so a body merely mentioning e.g. "intern program"
    doesn't knock out an otherwise-good senior role.

    Cuts LLM call volume. If nothing matches the include list (e.g. sparse
    titles), falls back to the recency-ordered list so the run isn't empty.
    """
    includes = [k.lower() for k in config.SEARCH_KEYWORDS]
    excludes = [k.lower() for k in config.EXCLUDE_KEYWORDS]

    kept: list[dict] = []
    for job in jobs:
        title = job["title"].lower()
        haystack = title + " " + (job.get("description") or "").lower()
        if any(ex in title for ex in excludes):
            continue
        if includes and not any(inc in haystack for inc in includes):
            continue
        kept.append(job)

    if not kept and jobs:
        logger.info("Keyword pre-filter matched nothing; using all scraped jobs.")
        return list(jobs)

    logger.info("Keyword pre-filter: %d → %d jobs.", len(jobs), len(kept))
    return kept


def _render_prompt(template: str, job: dict, resume_content: str) -> str:
    """Fill the scoring template. Uses str.replace (not .format) because the
    template and resume contain literal braces.
    """
    return (
        template
        .replace("{job_description}", truncate(job.get("description", ""), 6000))
        .replace("{resume_content}", truncate(resume_content, 6000))
    )


def score_jobs(jobs: list[dict], resume_content: str, *, progress_cb=None) -> list[dict]:
    """Score up to TOP_N_TO_RANK jobs with the LLM, then rank all jobs by score.

    Args:
        jobs: scraped jobs (already recency/exclude filtered).
        resume_content: plain-ish text of the master resume.
        progress_cb: optional callable invoked once per scored job (for a
            progress bar in the CLI).

    Returns the full job list sorted by score descending, with rank assigned.
    """
    template = read_text(_SCORING_PROMPT_PATH)

    candidates = prefilter(jobs)
    to_score = candidates[: config.TOP_N_TO_RANK]
    logger.info("Scoring %d job(s) with model %s.", len(to_score), config.SCORING_MODEL)

    scored_ids = set()
    for job in to_score:
        prompt = _render_prompt(template, job, resume_content)
        result = llm.call_llm_json(
            config.SCORING_MODEL,
            prompt,
            fallback={"score": 0, "reasons": [], "missing": [], "ats_keywords": []},
        )
        _apply_score(job, result)
        scored_ids.add(job["id"])
        if progress_cb:
            progress_cb(job)

    # Jobs not sent to the LLM get a neutral 0 so they sort to the bottom.
    for job in jobs:
        if job["id"] not in scored_ids and job.get("score") is None:
            job["score"] = 0
            job["score_reason"] = "Not scored (below TOP_N_TO_RANK cutoff)"

    ranked = sorted(jobs, key=lambda j: (j.get("score") or 0), reverse=True)
    for i, job in enumerate(ranked, start=1):
        job["rank"] = i
    return ranked


def _apply_score(job: dict, result: dict) -> None:
    """Coerce LLM JSON into the job's score fields, defensively."""
    raw_score = result.get("score", 0)
    try:
        score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))

    reasons = result.get("reasons") or []
    missing = result.get("missing") or []
    ats = result.get("ats_keywords") or []

    job["score"] = score
    job["score_reason"] = "; ".join(str(r) for r in reasons)
    job["missing"] = "; ".join(str(m) for m in missing)
    job["ats_keywords"] = ", ".join(str(k) for k in ats)
    if job.get("status") == "scraped":
        job["status"] = "scored"
