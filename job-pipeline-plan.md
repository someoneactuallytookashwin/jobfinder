# Job Application Pipeline — Project Plan
> Feed this entire document to Claude Code to scaffold the project.

---

## Project Overview

Build a local, on-demand job application pipeline that:
- Scrapes 50 jobs from the last 24 hours across multiple job boards
- Scores and ranks all 50 jobs against a master LaTeX resume
- Tailors resumes in batches of 10 via a CLI trigger
- Compiles tailored `.tex` files to PDF using pdflatex
- Outputs a running Excel sheet with all job data and rankings

**Stack:** Python, Ollama (local LLM), pdflatex, openpyxl, feedparser, playwright
**Primary machine:** ASUS, RTX 3060, 16GB RAM, Windows
**Models:** `qwen2.5:3b` for scoring, `qwen2.5:7b` for tailoring

---

## Project Structure

```
job-pipeline/
│
├── main.py                  # CLI entrypoint
├── config.py                # All user settings in one place
├── requirements.txt         # Python dependencies
├── .env                     # API keys, paths (gitignored)
├── .gitignore
│
├── resume/
│   ├── master.tex           # Your master LaTeX resume (drop here)
│   └── output/              # Generated tailored resumes
│       ├── batch_1/
│       │   ├── company_role.tex
│       │   └── company_role.pdf
│       └── batch_2/
│
├── data/
│   ├── jobs_raw.json        # Raw scraped jobs (per run)
│   ├── jobs_scored.json     # Scored + ranked jobs
│   └── results.xlsx         # Master Excel output (appended each run)
│
├── pipeline/
│   ├── __init__.py
│   ├── scraper.py           # Job scraping logic
│   ├── scorer.py            # LLM fit scoring
│   ├── tailor.py            # LLM resume tailoring
│   ├── compiler.py          # pdflatex compilation
│   ├── excel.py             # Excel read/write
│   └── utils.py             # Shared helpers
│
└── prompts/
    ├── scoring_prompt.txt   # Prompt template for fit scoring
    └── tailoring_prompt.txt # Prompt template for resume tailoring
```

---

## config.py — All Settings

```python
# config.py
# Edit these values before first run

# --- Models ---
SCORING_MODEL = "qwen2.5:3b"
TAILORING_MODEL = "qwen2.5:7b"
OLLAMA_BASE_URL = "http://localhost:11434"

# --- Pipeline Settings ---
SCRAPE_LIMIT = 50               # Total jobs to scrape per run
BATCH_SIZE = 10                 # Resumes to tailor per batch trigger
LOOKBACK_HOURS = 24             # Only jobs posted in last N hours
TOP_N_TO_RANK = 30              # Jobs sent to LLM scorer after keyword filter

# --- Job Search ---
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

# --- Sources ---
# Toggle which sources to scrape
SOURCES = {
    "indeed_rss": True,
    "linkedin": False,      # Requires playwright, more fragile
    "simplify": False,      # Requires playwright, more fragile
}
```

---

## CLI Interface — main.py

The CLI has three commands:

```bash
# Step 1 — Scrape and score all 50 jobs (run once per session)
python main.py scrape

# Step 2 — Tailor batch 1 (jobs ranked 1-10)
python main.py batch --n 1

# Step 3 — Tailor batch 2 (jobs ranked 11-20)
python main.py batch --n 2

# Optional — Re-score without re-scraping
python main.py score

# Optional — View current rankings in terminal
python main.py status
```

### scrape command
1. Pull jobs from enabled sources in config
2. Deduplicate by URL
3. Filter by `LOOKBACK_HOURS`
4. Pre-filter by `EXCLUDE_KEYWORDS` (no LLM, just string match)
5. Save raw to `data/jobs_raw.json`
6. Run LLM scorer on top `TOP_N_TO_RANK`
7. Save scored + ranked to `data/jobs_scored.json`
8. Write/update `data/results.xlsx`
9. Print summary table to terminal

### batch command
1. Read `data/jobs_scored.json`
2. Slice jobs for the requested batch number
3. For each job in batch:
   - Send job description + master resume to LLM
   - Get back modified LaTeX
   - Save `.tex` to `resume/output/batch_N/`
   - Compile to PDF with pdflatex
   - Update Excel row with status "Tailored"
4. Print completion summary

---

## pipeline/scraper.py

### Indeed RSS (primary source)
- Use `feedparser` to pull Indeed RSS feeds per keyword
- Indeed RSS format: `https://www.indeed.com/rss?q={keyword}&l={location}&sort=date`
- Parse: title, company, location, URL, date posted, description snippet
- Filter by date using `LOOKBACK_HOURS`

### LinkedIn + Simplify (optional, playwright)
- Use `playwright` in headless mode
- Scroll job listings, extract cards
- Be conservative — add random delays between requests (2–5s)
- Only enable if RSS isn't sufficient

### Output shape per job
```python
{
    "id": "hash of URL",
    "title": "Senior Product Designer",
    "company": "Acme Corp",
    "location": "Remote",
    "url": "https://...",
    "posted_at": "2025-01-01T10:00:00",
    "description": "Full job description text...",
    "source": "indeed_rss",
    "score": None,          # filled by scorer
    "score_reason": None,   # filled by scorer
    "batch": None,          # filled when tailored
    "status": "scraped"     # scraped | scored | tailored
}
```

---

## pipeline/scorer.py

### Pre-filter (no LLM)
- String match title against `SEARCH_KEYWORDS`
- Exclude if title contains `EXCLUDE_KEYWORDS`
- This cuts the LLM call volume before scoring

### LLM Scoring
- Load `prompts/scoring_prompt.txt`
- For each job, send: job description + resume content
- Ask LLM to return JSON:
```json
{
  "score": 8,
  "reasons": ["Strong React mention", "Matches seniority level"],
  "missing": ["No Figma mentioned explicitly"],
  "ats_keywords": ["product design", "user research", "figma"]
}
```
- Sort all jobs by score descending
- Save to `jobs_scored.json`

### prompts/scoring_prompt.txt
```
You are an expert technical recruiter and ATS specialist.

Given the job description and resume below, score the fit from 1-10.

Rules:
- Score based on skills match, seniority, and role alignment
- Be strict. A 7 means genuinely strong match.
- Return ONLY valid JSON, no explanation outside the JSON

Return this exact structure:
{
  "score": <integer 1-10>,
  "reasons": [<up to 3 short strings>],
  "missing": [<up to 3 skills in JD not in resume>],
  "ats_keywords": [<up to 10 keywords from JD to add to resume>]
}

JOB DESCRIPTION:
{job_description}

RESUME:
{resume_content}
```

---

## pipeline/tailor.py

### Logic
- Read `master.tex`
- For each job in the batch:
  - Send to LLM with tailoring prompt
  - LLM returns modified LaTeX
  - Validate output is valid LaTeX (basic checks — has `\begin{document}`, `\end{document}`)
  - If invalid, retry once
  - Save to `resume/output/batch_N/companyname_role.tex`

### What the LLM is allowed to change
- Professional summary / objective section
- Bullet points under each role (rephrase, reorder, add keywords)
- Skills section (reorder, add missing ATS keywords)

### What the LLM must NOT change
- Any LaTeX commands or formatting
- Document structure
- Your name, contact info, dates, company names, job titles

### prompts/tailoring_prompt.txt
```
You are an expert resume writer and LaTeX developer.

Your job is to tailor the resume below for the target job description.
You must return ONLY the complete modified LaTeX source, nothing else.
No explanation, no markdown, no code fences. Raw LaTeX only.

STRICT RULES:
1. Do NOT change any LaTeX formatting commands
2. Do NOT change document structure, \begin, \end, or any preamble
3. Do NOT change the candidate's name, contact info, dates, or job titles
4. ONLY modify: summary section, bullet point text, skills section
5. Naturally incorporate these ATS keywords: {ats_keywords}
6. Match the seniority and tone of the job description
7. Keep all bullet points concise and achievement-oriented

TARGET JOB:
Title: {job_title}
Company: {company}
Description: {job_description}

RESUME SOURCE (LaTeX):
{resume_latex}
```

---

## pipeline/compiler.py

### Logic
- Run `pdflatex` on each `.tex` file
- Suppress output unless error
- Check for output `.pdf` — if missing, flag as compile error in Excel
- Clean up auxiliary files (`.aux`, `.log`, `.out`)

```python
import subprocess

def compile_tex(tex_path: str, output_dir: str) -> bool:
    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-output-directory", output_dir, tex_path],
        capture_output=True,
        text=True
    )
    return result.returncode == 0
```

> Note: pdflatex must be installed. On Windows install MiKTeX from miktex.org.
> On first compile it may download packages automatically — let it.

---

## pipeline/excel.py

### Columns in results.xlsx

| Column | Description |
|---|---|
| Run Date | When the pipeline ran |
| Rank | Score rank (1 = best) |
| Score | LLM fit score (1–10) |
| Title | Job title |
| Company | Company name |
| Location | Remote / city |
| Source | indeed_rss / linkedin etc |
| Posted | When job was posted |
| URL | Direct link to job |
| Score Reasons | Why it scored well |
| Missing Skills | What resume lacks |
| ATS Keywords | Keywords to add |
| Status | scraped / scored / tailored |
| Resume File | Path to tailored PDF |

### Behavior
- On first run: create `results.xlsx` with headers
- On subsequent runs: append new rows, don't overwrite
- Highlight top 10 rows in green automatically
- Highlight "tailored" status rows in blue

---

## requirements.txt

```
ollama
feedparser
playwright
openpyxl
python-dotenv
requests
click
rich          # pretty terminal output
```

---

## Setup Instructions (for Claude Code)

1. Create the full project structure above
2. Install dependencies: `pip install -r requirements.txt`
3. Install playwright browsers: `playwright install chromium`
4. Install MiKTeX (Windows): https://miktex.org/download
5. Pull Ollama models:
   ```bash
   ollama pull qwen2.5:3b
   ollama pull qwen2.5:7b
   ```
6. Copy master resume to `resume/master.tex`
7. Edit `config.py` with your search keywords and location
8. Run: `python main.py scrape`

---

## Notes for Claude Code

- Use `click` for the CLI, `rich` for terminal tables and progress bars
- All LLM calls go through a single `llm.py` wrapper that handles Ollama API calls and retries
- JSON parsing from LLM responses should always use try/except with a fallback
- LaTeX validation before saving: check for `\begin{document}` and `\end{document}` at minimum
- Log all LLM calls to a `pipeline.log` file for debugging
- Never hardcode paths — always use `config.py` values
- The `data/` and `resume/output/` directories should be gitignored but created on first run
- `master.tex` should be gitignored too (contains personal info)
