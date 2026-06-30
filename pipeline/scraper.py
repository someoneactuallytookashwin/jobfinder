"""Job scraping.

Sources (each isolated — a failure in one never kills the run):
  - indeed_rss   : Indeed RSS via feedparser. Fully reliable.
  - linkedin     : LinkedIn guest "seeMoreJobPostings" JSON/HTML endpoint
                   (requests-based, no login). More stable than DOM scraping,
                   but LinkedIn may rate-limit.
  - simplify     : SimplifyJobs public GitHub lists (markdown tables). Reliable,
                   but those lists skew new-grad/SWE, so design roles are sparse.
  - wellfound    : Best-effort Playwright scrape. Login-gated + anti-bot, so it
                   may return little or nothing without an authenticated session.
  - ycombinator  : YC's "Ask HN: Who is hiring?" monthly thread via the public
                   HN Algolia API (Work at a Startup itself is login-gated).

All scrapers return jobs in the canonical shape; scrape_all() dedupes by URL,
filters by LOOKBACK_HOURS, pre-filters by EXCLUDE_KEYWORDS, and caps at
SCRAPE_LIMIT.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config
from pipeline.utils import get_logger, hash_url, truncate

logger = get_logger()

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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
# Small HTTP / HTML helpers (stdlib only, no bs4 dependency)
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: int = 20) -> str:
    """GET a URL with a browser-ish User-Agent, return decoded body text."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _http_get_json(url: str, *, timeout: int = 20) -> dict:
    return json.loads(_http_get(url, timeout=timeout))


def _strip_tags(fragment: str) -> str:
    """Strip HTML tags and unescape entities from a fragment."""
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _search(pattern: str, text: str) -> str:
    m = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


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

        time.sleep(1)  # be polite to the feed endpoint

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
# LinkedIn — guest "seeMoreJobPostings" endpoint (requests/urllib, no login)
# ---------------------------------------------------------------------------

_LI_GUEST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)


def scrape_linkedin() -> list[dict]:
    """Scrape LinkedIn's public guest job-search endpoint.

    This endpoint returns HTML <li> job cards without requiring a login, which
    is far more stable than scraping the rendered page DOM. LinkedIn may still
    rate-limit aggressive use, so we keep pagination shallow and add delays.

    f_TPR=r86400 restricts to the last 24h (further filtered by LOOKBACK_HOURS).
    """
    jobs: list[dict] = []
    pages = 2  # start=0 and start=25 → up to ~50 cards per keyword

    for keyword in config.SEARCH_KEYWORDS:
        for page in range(pages):
            params = urllib.parse.urlencode(
                {
                    "keywords": keyword,
                    "location": config.LOCATION,
                    "f_TPR": "r86400",
                    "start": page * 25,
                }
            )
            url = f"{_LI_GUEST_URL}?{params}"
            logger.info("LinkedIn guest fetch: %s", url)
            try:
                body = _http_get(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LinkedIn fetch failed for '%s' (start=%d): %s",
                               keyword, page * 25, exc)
                break  # stop paginating this keyword on error

            cards = re.split(r"<li>", body)
            found = 0
            for card in cards:
                job = _parse_linkedin_card(card)
                if job:
                    jobs.append(job)
                    found += 1
            logger.info("LinkedIn '%s' start=%d → %d cards.", keyword, page * 25, found)
            if found == 0:
                break  # no more results
            time.sleep(2)

    logger.info("LinkedIn produced %d raw jobs.", len(jobs))
    return jobs


def _parse_linkedin_card(card: str) -> dict | None:
    link = _search(r'base-card__full-link"[^>]*href="([^"]+)"', card) or \
        _search(r'href="(https://www\.linkedin\.com/jobs/view/[^"]+)"', card)
    if not link:
        return None
    link = html.unescape(link).split("?")[0]

    title = _strip_tags(_search(r'base-search-card__title">(.*?)</h3>', card))
    company = _strip_tags(_search(r'base-search-card__subtitle">(.*?)</h4>', card))
    location = _strip_tags(_search(r'job-search-card__location">(.*?)</span>', card))
    posted = _search(r'datetime="([^"]+)"', card)
    posted_at = _normalize_date(posted)

    if not title:
        return None
    return _new_job(
        title=title,
        company=company,
        location=location or config.LOCATION,
        url=link,
        posted_at=posted_at,
        description=title,  # listing endpoint has no full JD
        source="linkedin",
    )


def _normalize_date(value: str) -> str | None:
    """Turn a 'YYYY-MM-DD' (or ISO) string into an ISO timestamp, else None."""
    if not value:
        return None
    try:
        if len(value) == 10:  # date only
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Simplify — public GitHub job lists (markdown tables)
# ---------------------------------------------------------------------------

# SimplifyJobs maintains these public lists. They skew new-grad / SWE, so design
# roles are sparse, but the data is stable and auth-free.
_SIMPLIFY_README_URLS = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/README.md",
]


def scrape_simplify() -> list[dict]:
    """Parse SimplifyJobs' public GitHub README HTML tables into jobs.

    The README renders listings as HTML <table> rows (Company / Role / Location
    / Application / Age). Those lists skew new-grad / SWE, so we keyword-filter
    to design-relevant titles here — otherwise hundreds of SWE rows would crowd
    out the design roles before the SCRAPE_LIMIT cap.
    """
    body = ""
    for url in _SIMPLIFY_README_URLS:
        try:
            logger.info("Simplify GitHub fetch: %s", url)
            body = _http_get(url)
            if body.strip():
                break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Simplify fetch failed (%s): %s", url, exc)
    if not body:
        return []

    includes = [k.lower() for k in config.SEARCH_KEYWORDS] + ["design"]
    jobs: list[dict] = []
    total_rows = 0
    last_company = ""

    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL | re.IGNORECASE)
        if len(cells) < 4:
            continue  # header rows use <th>, not <td>
        total_rows += 1

        company_cell, role_cell, location_cell, app_cell = cells[0], cells[1], cells[2], cells[3]

        company = _strip_tags(company_cell).replace("🔥", "").strip()
        # "↳" means "same company as the row above".
        if not company or "↳" in company:
            company = last_company
        else:
            last_company = company

        title = _strip_tags(role_cell)
        if not title:
            continue
        if not any(inc in title.lower() for inc in includes):
            continue  # keep only design-relevant roles

        location = _strip_tags(
            location_cell.replace("</br>", ", ").replace("<br>", ", ").replace("<br/>", ", ")
        )
        # Prefer the employer application link; fall back to the Simplify company link.
        link = _first_href(app_cell) or _first_href(company_cell)
        if not link:
            continue

        jobs.append(
            _new_job(
                title=title,
                company=company,
                location=location or "United States",
                url=link,
                posted_at=None,  # README ages are relative ("2d"), unreliable to parse
                description=f"{title} at {company}. {location}".strip(),
                source="simplify",
            )
        )

    logger.info(
        "Simplify parsed %d table rows → %d design-relevant jobs.", total_rows, len(jobs)
    )
    return jobs


def _first_href(cell: str) -> str:
    """First URL in an HTML cell."""
    m = re.search(r'href="([^"]+)"', cell)
    if m:
        return html.unescape(m.group(1)).split("?")[0]
    return ""


# ---------------------------------------------------------------------------
# YCombinator — via "Ask HN: Who is hiring?" (HN Algolia API, public)
# ---------------------------------------------------------------------------

# Newest-first list of the official "whoishiring" account's stories, so we always
# grab the *latest* monthly "Who is hiring?" thread (relevance search can return a
# popular old one whose comments are all outside the lookback window).
_HN_SEARCH = (
    "https://hn.algolia.com/api/v1/search_by_date?"
    "tags=story,author_whoishiring&hitsPerPage=20"
)
_HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"


def scrape_ycombinator() -> list[dict]:
    """Scrape the latest YC 'Ask HN: Who is hiring?' thread.

    Work at a Startup hides listings behind a login, so the monthly HN hiring
    thread (posted by YC's 'whoishiring' account) is the reliable public proxy.
    Each top-level comment is one posting; we keyword-filter and best-effort
    parse company/title/location out of the free text.
    """
    try:
        results = _http_get_json(_HN_SEARCH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HN search failed: %s", exc)
        return []

    story_id = None
    for hit in results.get("hits", []):
        title = (hit.get("title") or "").lower()
        if "who is hiring" in title and hit.get("author") == "whoishiring":
            story_id = hit.get("objectID")
            break
    if not story_id:
        logger.warning("Could not locate a 'Who is hiring?' thread on HN.")
        return []

    try:
        thread = _http_get_json(_HN_ITEM.format(id=story_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("HN thread fetch failed: %s", exc)
        return []

    # YC uses its own time window (config.YC_LOOKBACK_DAYS), not LOOKBACK_HOURS,
    # because the thread's posts cluster on day 1-2.
    cutoff_ts = (
        datetime.now(timezone.utc) - timedelta(days=config.YC_LOOKBACK_DAYS)
    ).timestamp()

    includes = [k.lower() for k in config.SEARCH_KEYWORDS] + ["design"]
    jobs: list[dict] = []
    for comment in thread.get("children", []):
        if len(jobs) >= config.YC_MAX_POSTINGS:
            break

        created_i = comment.get("created_at_i")
        if created_i and created_i < cutoff_ts:
            continue  # outside YC's own lookback window

        text_html = comment.get("text") or ""
        text = _strip_tags(text_html)
        if not text:
            continue
        if not any(inc in text.lower() for inc in includes):
            continue

        first_line = text.split(". ")[0]
        # HN hiring posts often look like: "Company | Role | Location | ..."
        parts = [p.strip() for p in re.split(r"\s*\|\s*", first_line) if p.strip()]
        company = parts[0] if parts else "Unknown (HN)"
        title = parts[1] if len(parts) > 1 else first_line[:80]
        location = next((p for p in parts[2:] if _looks_like_location(p)), "")

        link = _first_url(text_html) or f"https://news.ycombinator.com/item?id={comment.get('id')}"
        posted_at = _normalize_date((comment.get("created_at") or "")[:19])

        jobs.append(
            _new_job(
                title=title,
                company=company,
                location=location or "United States",
                url=link,
                posted_at=posted_at,
                description=truncate(text, 4000),
                source="ycombinator",
            )
        )

    logger.info(
        "YCombinator (HN Who-is-hiring) produced %d jobs (last %d days, cap %d).",
        len(jobs), config.YC_LOOKBACK_DAYS, config.YC_MAX_POSTINGS,
    )
    return jobs


def _looks_like_location(part: str) -> str:
    """Heuristic: does this free-text segment look like a location?

    Matches 'remote'/'USA'/'United States' (case-insensitive) or a US-style
    ", XX" state code (uppercase only, so it doesn't match lowercase prose).
    """
    if re.search(r"\b(remote|usa|united states)\b", part, re.I):
        return True
    return bool(re.search(r",\s*[A-Z]{2}\b", part))


def _first_url(text_html: str) -> str:
    m = re.search(r'href="([^"]+)"', text_html or "")
    if m:
        return html.unescape(m.group(1))
    m = re.search(r"https?://\S+", _strip_tags(text_html or ""))
    return m.group(0).rstrip(".,)") if m else ""


# ---------------------------------------------------------------------------
# Wellfound — best-effort Playwright scrape (login-gated, low reliability)
# ---------------------------------------------------------------------------

def scrape_wellfound() -> list[dict]:
    """Best-effort Wellfound scrape via Playwright.

    Wellfound (ex-AngelList Talent) gates most listings behind a login and runs
    aggressive anti-bot, so this commonly returns little or nothing without an
    authenticated session. It fails soft (returns []) rather than erroring.
    """
    jobs: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed; skipping Wellfound. "
                       "Run `playwright install chromium`.")
        return jobs

    import random

    role_slug = re.sub(r"\s+", "-", (config.SEARCH_KEYWORDS[0] if config.SEARCH_KEYWORDS else "").lower())
    search_url = f"https://wellfound.com/role/r/{role_slug}"

    logger.info("Wellfound scrape (best-effort): %s", search_url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=_USER_AGENT)
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(random.uniform(3, 5))

            cards = page.query_selector_all('[data-test="JobSearchResult"], div.styles_component__job')
            for card in cards:
                try:
                    link_el = card.query_selector("a[href*='/jobs/']")
                    url = link_el.get_attribute("href") if link_el else None
                    if not url:
                        continue
                    if url.startswith("/"):
                        url = "https://wellfound.com" + url
                    title = _node_text(card, "a[href*='/jobs/']")
                    company = _node_text(card, "[data-test='startup-link'], h2, h3")
                    jobs.append(
                        _new_job(
                            title=title or "Role",
                            company=company,
                            location=config.LOCATION,
                            url=url,
                            posted_at=None,
                            description=title or "",
                            source="wellfound",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping a Wellfound card: %s", exc)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wellfound scrape failed (likely login/anti-bot): %s", exc)

    if not jobs:
        logger.info(
            "Wellfound returned no jobs — this is expected without a logged-in "
            "session. Other sources will still populate results."
        )
    logger.info("Wellfound produced %d raw jobs.", len(jobs))
    return jobs


def _node_text(card, selector: str) -> str:
    el = card.query_selector(selector)
    return el.inner_text().strip() if el else ""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_SOURCE_FUNCS = {
    "indeed_rss": scrape_indeed_rss,
    "linkedin": scrape_linkedin,
    "simplify": scrape_simplify,
    "wellfound": scrape_wellfound,
    "ycombinator": scrape_ycombinator,
}


#: YCombinator is treated as its own category. It's kept out of the main
#: SCRAPE_LIMIT cap and the LOOKBACK_HOURS window (it has YC_LOOKBACK_DAYS /
#: YC_MAX_POSTINGS instead) so it can be reviewed separately. It's still scored
#: and ranked in the same pipeline, and lands on its own Excel tab.
YC_SOURCE = "ycombinator"


def scrape_all() -> list[dict]:
    """Run every enabled source (isolated) and return the jobs to score.

    Two tracks, combined at the end:
      - Main sources: dedupe → LOOKBACK_HOURS recency → exclude → round-robin →
        cap at SCRAPE_LIMIT.
      - YCombinator: its own category — dedupe → exclude → cap at YC_MAX_POSTINGS
        (its time window is applied inside scrape_ycombinator). Does NOT consume
        the main SCRAPE_LIMIT.
    """
    raw: list[dict] = []
    for name, enabled in config.SOURCES.items():
        if not enabled:
            continue
        func = _SOURCE_FUNCS.get(name)
        if func is None:
            logger.warning("Unknown source in config (no scraper): %s", name)
            continue
        try:
            raw.extend(func())
        except Exception as exc:  # noqa: BLE001 - one bad source shouldn't kill the run
            logger.error("Source '%s' crashed and was skipped: %s", name, exc)

    deduped = _dedupe_by_url(raw)
    yc_jobs = [j for j in deduped if j["source"] == YC_SOURCE]
    main_jobs = [j for j in deduped if j["source"] != YC_SOURCE]

    # Main track.
    recent = _filter_by_recency(main_jobs)
    filtered = _prefilter_excluded(recent)
    balanced = _interleave_by_source(filtered)
    main_capped = balanced[: config.SCRAPE_LIMIT]

    # YCombinator track (own window already applied; just exclude + cap here).
    yc_capped = _prefilter_excluded(yc_jobs)[: config.YC_MAX_POSTINGS]

    combined = main_capped + yc_capped
    logger.info(
        "Scrape pipeline → main: %d raw → %d recent → %d after exclude → %d capped "
        "(limit %d). YCombinator: %d → %d capped (limit %d). Combined: %d.",
        len(main_jobs), len(recent), len(filtered), len(main_capped), config.SCRAPE_LIMIT,
        len(yc_jobs), len(yc_capped), config.YC_MAX_POSTINGS, len(combined),
    )
    return combined


def _interleave_by_source(jobs: list[dict]) -> list[dict]:
    """Round-robin jobs across their sources so the SCRAPE_LIMIT cap is shared
    fairly. Otherwise whichever source runs first (e.g. LinkedIn) would fill the
    entire cap and starve the others (e.g. YCombinator). Order within each source
    is preserved.
    """
    from collections import OrderedDict

    buckets: "OrderedDict[str, list[dict]]" = OrderedDict()
    for job in jobs:
        buckets.setdefault(job["source"], []).append(job)

    iters = [iter(bucket) for bucket in buckets.values()]
    out: list[dict] = []
    while iters:
        still_active = []
        for it in iters:
            try:
                out.append(next(it))
                still_active.append(it)
            except StopIteration:
                continue
        iters = still_active
    return out


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
    """Keep jobs posted within LOOKBACK_HOURS. Jobs with no date are always kept.

    YCombinator never reaches here — it's split out in scrape_all and uses its
    own window (YC_LOOKBACK_DAYS).
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
