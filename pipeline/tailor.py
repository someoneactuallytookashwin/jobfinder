"""LLM resume tailoring.

For each job in a batch, sends the master LaTeX resume + job details to the
tailoring model, validates the returned LaTeX, retries once if invalid, and
writes the .tex into resume/output/batch_N/.
"""

from __future__ import annotations

import os

import config
from pipeline import llm
from pipeline.utils import batch_dir, get_logger, read_text, slugify, truncate, write_text

logger = get_logger()

_TAILORING_PROMPT_PATH = os.path.join("prompts", "tailoring_prompt.txt")


def is_valid_latex(text: str) -> bool:
    """Minimal structural check: must contain \\begin{document} and \\end{document}."""
    return r"\begin{document}" in text and r"\end{document}" in text


def _strip_fences(text: str) -> str:
    """Remove any stray markdown code fences the model may add despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop first fence line and a trailing fence line if present.
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _render_prompt(template: str, job: dict, resume_latex: str) -> str:
    """Fill the tailoring template via str.replace (LaTeX is full of braces)."""
    return (
        template
        .replace("{ats_keywords}", job.get("ats_keywords") or "")
        .replace("{job_title}", job.get("title", ""))
        .replace("{company}", job.get("company", ""))
        .replace("{job_description}", truncate(job.get("description", ""), 6000))
        .replace("{resume_latex}", resume_latex)
    )


def tailor_job(job: dict, resume_latex: str, batch_n: int) -> str | None:
    """Tailor one job's resume. Returns the saved .tex path, or None on failure."""
    template = read_text(_TAILORING_PROMPT_PATH)
    prompt = _render_prompt(template, job, resume_latex)

    tailored = None
    for attempt in (1, 2):  # validate output; retry once if invalid
        logger.info(
            "Tailoring '%s @ %s' (attempt %d) with model %s.",
            job.get("title"), job.get("company"), attempt, config.TAILORING_MODEL,
        )
        try:
            raw = llm.call_llm(config.TAILORING_MODEL, prompt)
        except llm.LLMError as exc:
            logger.error("Tailoring LLM call failed: %s", exc)
            return None

        candidate = _strip_fences(raw)
        if is_valid_latex(candidate):
            tailored = candidate
            break
        logger.warning(
            "Tailored output failed LaTeX validation on attempt %d "
            "(missing \\begin/\\end{document}).",
            attempt,
        )

    if tailored is None:
        logger.error(
            "Giving up on '%s @ %s' — invalid LaTeX after retry.",
            job.get("title"), job.get("company"),
        )
        return None

    filename = f"{slugify(job.get('company', 'company'))}_{slugify(job.get('title', 'role'))}.tex"
    out_dir = batch_dir(batch_n)
    tex_path = os.path.join(out_dir, filename)
    write_text(tex_path, tailored)
    logger.info("Wrote tailored resume: %s", tex_path)
    return tex_path


def load_master_resume() -> str:
    """Read the master LaTeX resume, raising a friendly error if missing."""
    if not os.path.exists(config.MASTER_RESUME_PATH):
        raise FileNotFoundError(
            f"Master resume not found at '{config.MASTER_RESUME_PATH}'. "
            "Drop your LaTeX resume there first (see the README)."
        )
    return read_text(config.MASTER_RESUME_PATH)
