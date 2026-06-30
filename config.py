# config.py
# Edit these values before first run.
#
# >>> IMPORTANT <<<
# SEARCH_KEYWORDS, LOCATION, and EXCLUDE_KEYWORDS below ship with the original
# author's job search settings. CHANGE THEM to match your own role and location
# before running `python main.py scrape`, or you'll scrape jobs for the wrong
# role in the wrong place. See the README ("Before you run this — make it yours").

# --- Models ---
SCORING_MODEL = "qwen2.5:3b"
# NOTE: this must exactly match the model tag you `ollama pull`. The README tells
# you to pull `qwen2.5:7b-instruct-q5_K_M` (a quantized build that fits a 16GB /
# RTX 3060 machine), so that's the tag used here.
TAILORING_MODEL = "qwen2.5:7b-instruct-q5_K_M"
OLLAMA_BASE_URL = "http://localhost:11434"

# --- Pipeline Settings ---
SCRAPE_LIMIT = 50               # Total jobs to scrape per run
BATCH_SIZE = 10                 # Resumes to tailor per batch trigger
# Only keep jobs posted in the last N hours. Applies to dated sources like
# LinkedIn — set to 2 weeks for a healthy pool. (The YC/HN "Who is hiring?"
# thread bypasses this window entirely; see _RECENCY_EXEMPT_SOURCES in
# scraper.py — its posts cluster on day 1-2 so a time window can't capture it.)
# Lower toward 24-72 if you only want very fresh LinkedIn postings.
LOOKBACK_HOURS = 336            # 14 days
TOP_N_TO_RANK = 30              # Jobs sent to LLM scorer after keyword filter

# --- Job Search ---
# >>> CHANGE THESE THREE to match your own job search. <<<
SEARCH_KEYWORDS = [
    "product designer",
    "design engineer",
    "UX designer",
]
# Open to in-person roles anywhere in the US *and* remote. The scrapers take a
# single location string, so "United States" is the widest net for a nationwide
# search (Indeed returns both on-site US postings and US-tagged remote jobs).
# If you ever want remote-only, set this to "Remote".
LOCATION = "United States"
EXCLUDE_KEYWORDS = [            # Filter out irrelevant titles
    "junior",
    "intern",
    "manager",
]

# --- Paths ---
MASTER_RESUME_PATH = "resume/master.tex"
OUTPUT_DIR = "resume/output"
DATA_DIR = "data"
EXCEL_PATH = "data/results.xlsx"
RAW_JSON_PATH = "data/jobs_raw.json"
SCORED_JSON_PATH = "data/jobs_scored.json"
LOG_PATH = "pipeline.log"

# --- LLM call tuning ---
LLM_MAX_RETRIES = 3             # Retries per LLM call before giving up
LLM_TIMEOUT = 120               # Seconds to wait for a single Ollama response

# --- Sources ---
# Toggle which sources to scrape. Reliability varies a LOT (see README
# "Sources status"). Verified live 2026-06-30:
SOURCES = {
    # Indeed killed public RSS — the feed now returns HTTP 403 and yields
    # nothing. Left on (it fails soft) in case it's ever restored, but don't
    # count on it. LinkedIn is the real workhorse now.
    "indeed_rss": True,
    "linkedin": True,       # WORKS WELL — guest JSON endpoint, no login. May rate-limit.
    "simplify": True,       # Works; SimplifyJobs GitHub lists skew new-grad/SWE/hardware.
    "ycombinator": True,    # YC "Who is hiring?" HN thread. Monthly thread, so the
                            # 24h LOOKBACK_HOURS filter drops most of it mid-month —
                            # raise LOOKBACK_HOURS to capture more (see README).
    "wellfound": True,      # Best-effort Playwright; usually empty without a login
                            # session AND requires `playwright install chromium`.
}
