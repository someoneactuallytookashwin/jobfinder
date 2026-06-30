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
LOOKBACK_HOURS = 24             # Only jobs posted in last N hours
TOP_N_TO_RANK = 30              # Jobs sent to LLM scorer after keyword filter

# --- Job Search ---
# >>> CHANGE THESE THREE to match your own job search. <<<
SEARCH_KEYWORDS = [
    "product designer",
    "UX designer",
    "senior product designer",
]
LOCATION = "Remote"             # or "New York", "London" etc
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
# Toggle which sources to scrape
SOURCES = {
    "indeed_rss": True,
    "linkedin": False,      # Requires playwright, more fragile
    "simplify": False,      # Requires playwright, more fragile
}
