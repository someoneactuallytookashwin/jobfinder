"""Job scraping.

Primary source: Indeed RSS via feedparser. LinkedIn / Simplify are optional
playwright-based scrapers, disabled by default in config (more fragile).

All scrapers return jobs in the shape described in the plan and the scrape
entrypoint dedupes by URL, filters by LOOKBACK_HOURS, and pre-filters by
EXCLUDE_KEYWORDS before anything hits the LLM.
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import config
from pipeline.utils import get_logger, hash_url, truncate

logger = get_logger()


def _new_job(*, title, company, location, url, posted_at, description, source) -> dict:
    """Build a job dict in the canonical pipeline shape."""
    return {
        "id": hash_url(url),
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": url.strip(),
        "posted_at": posted_at,            # ISO string or None
        "description": truncate(description or ""),
        "source": source,
        "score": None,                     # filled by scorer
        "score_reason": None,              # filled by scorer
        "missing": None,                   # filled by scorer
        "ats_keywords": None,              # filled by scorer
        "batch": None,                     # filled when tailored
        "resume_file": None,               # filled when tailored
        "status": "scraped",               # scraped | scored | tailored
    }


# ---------------------------------------------------------------------------
# Indeed RSS (primary)
# ---------------------------------------------------------------------------

def scrape_indeed_rss() -> list[dict]:
    """Pull Indeed RSS feeds, one per keyword in config.SEARCH_KEYWORDS.

    Indeed RSS URL: https://www.indeed.com/rss?q={keyword}&l={location}&sort=date
    """
    import feedparser

    jobs: list[dict] = []
    for keyword in config.SEARCH_KEYWORDS:
        params = urllib.parse.urlencode(
            {"q": keyword, "l": config.LOCATION, "sort": "date"}
        )
        feed_url = f"https://www.indeed.com/rss?{params}"
        logger.info("Indeed RSS fetch: %s", feed_url)

        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch Indeed RSS for '%s': %s", keyword, exc)
            continue

        if getattr(feed, "bozo", False):
            logger.warning(
                "Indeed RSS for '%s' returned a malformed feed (%s).",
                keyword, getattr(feed, "bozo_exception", "unknown"),
            )

        for entry in feed.entries:
            url = entry.get("link", "").strip()
            if not url:
                continue

            # Indeed puts "Company - Location" in the title or a source field.
            title = entry.get("title", "")
            company = entry.get("source", {}).get("title", "") if isinstance(
                entry.get("source"), dict
            ) else entry.get("author", "")

            posted_at = _parse_entry_date(entry)
            description = entry.get("summary", "") or entry.get("description", "")

            jobs.append(
                _new_job(
                    title=title,
                    company=company,
                    location=config.LOCATION,
                    url=url,
                    posted_at=posted_at,
                    description=description,
                    source="indeed_rss",
                )
            )

        # Be polite to the feed endpoint.
        time.sleep(1)

    logger.info("Indeed RSS produced %d raw jobs.", len(jobs))
    return jobs


def _parse_entry_date(entry) -> str | None:
    """Extract an ISO timestamp from a feedparser entry, if available."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# LinkedIn + Simplify (optional, playwright) — conservative, off by default
# ---------------------------------------------------------------------------

def scrape_linkedin() -> list[dict]:
    """Optional LinkedIn scraper (playwright, headless). Off by default.

    LinkedIn markup changes often, so treat this as best-effort. Random delays
    are used between actions to stay polite.
    """
    jobs: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed; skipping LinkedIn source.")
        return jobs

    import random

    keyword = config.SEARCH_KEYWORDS[0] if config.SEARCH_KEYWORDS else ""
    search_url = (
        "https://www.linkedin.com/jobs/search/?"
        + urllib.parse.urlencode(
            {"keywords": keyword, "location": config.LOCATION, "f_TPR": "r86400"}
        )
    )

    logger.info("LinkedIn scrape: %s", search_url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(search_url, timeout=30000)
            time.sleep(random.uniform(2, 5))

            # Scroll to load more cards.
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                time.sleep(random.uniform(2, 5))

            cards = page.query_selector_all("div.base-card")
            for card in cards:
                try:
                    title = _text(card, ".base-search-card__title")
                    company = _text(card, ".base-search-card__subtitle")
                    location = _text(card, ".job-search-card__location")
                    link_el = card.query_selector("a.base-card__full-link")
                    url = link_el.get_attribute("href") if link_el else None
                    if not url:
                        continue
                    jobs.append(
                        _new_job(
                            title=title,
                            company=company,
                            location=location or config.LOCATION,
                            url=url,
                            posted_at=None,
                            description=title,  # listing page has no full JD
                            source="linkedin",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping a LinkedIn card: %s", exc)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn scrape failed: %s", exc)

    logger.info("LinkedIn produced %d raw jobs.", len(jobs))
    return jobs


def scrape_simplify() -> list[dict]:
    """Optional Simplify scraper (playwright). Off by default — placeholder.

    Simplify's job board is heavily JS-driven and changes frequently. This is
    left as a conservative stub that returns nothing unless extended.
    """
    logger.info("Simplify source is enabled but not implemented; returning none.")
    return []


def _text(card, selector: str) -> str:
    el = card.query_selector(selector)
    return el.inner_text().strip() if el else ""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_SOURCE_FUNCS = {
    "indeed_rss": scrape_indeed_rss,
    "linkedin": scrape_linkedin,
    "simplify": scrape_simplify,
}


def scrape_all() -> list[dict]:
    """Run every enabled source, dedupe by URL, filter by recency, pre-filter
    by EXCLUDE_KEYWORDS, and cap at SCRAPE_LIMIT.
    """
    raw: list[dict] = []
    for name, enabled in config.SOURCES.items():
        if not enabled:
            continue
        func = _SOURCE_FUNCS.get(name)
        if func is None:
            logger.warning("Unknown source in config: %s", name)
            continue
        raw.extend(func())

    deduped = _dedupe_by_url(raw)
    recent = _filter_by_recency(deduped)
    filtered = _prefilter_excluded(recent)

    capped = filtered[: config.SCRAPE_LIMIT]
    logger.info(
        "Scrape pipeline: %d raw → %d deduped → %d recent → %d after exclude "
        "→ %d after cap.",
        len(raw), len(deduped), len(recent), len(filtered), len(capped),
    )
    return capped


def _dedupe_by_url(jobs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for job in jobs:
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        out.append(job)
    return out


def _filter_by_recency(jobs: list[dict]) -> list[dict]:
    """Keep jobs posted within LOOKBACK_HOURS. Jobs with no date are kept
    (RSS feeds don't always include reliable timestamps).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.LOOKBACK_HOURS)
    out: list[dict] = []
    for job in jobs:
        posted = job.get("posted_at")
        if not posted:
            out.append(job)
            continue
        try:
            dt = datetime.fromisoformat(posted)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                out.append(job)
        except ValueError:
            out.append(job)
    return out


def _prefilter_excluded(jobs: list[dict]) -> list[dict]:
    """Drop jobs whose title contains any EXCLUDE_KEYWORDS (string match, no LLM)."""
    excludes = [k.lower() for k in config.EXCLUDE_KEYWORDS]
    out: list[dict] = []
    for job in jobs:
        title = job["title"].lower()
        if any(ex in title for ex in excludes):
            continue
        out.append(job)
    return out
