# Job Application Pipeline

A local, run-it-yourself tool that helps you apply to jobs faster. It:

1. **Scrapes** recent job postings (last 24 hours) from job boards.
2. **Scores** each job from 1–10 against your résumé using a local AI model.
3. **Ranks** them so you see the best matches first.
4. **Tailors** your résumé to the top jobs — rewriting the summary, bullet points,
   and skills section to match each role — in batches of 10.
5. **Compiles** each tailored résumé to a PDF.
6. **Tracks everything** in a tidy Excel sheet (`data/results.xlsx`).

Everything runs **on your own machine**. The AI runs locally via [Ollama](https://ollama.com),
so no job descriptions or résumé data are sent to any cloud service.

---

## ⚠️ Before you run this — make it yours

**This repo ships with the original author's job-search settings.** If you run it as-is,
you'll get results for *their* role in *their* location — not yours.

Open [`config.py`](config.py) and change **these three values** before running `scrape`:

```python
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
```

- **`SEARCH_KEYWORDS`** — the job titles you actually want (e.g. `"data engineer"`, `"backend developer"`).
- **`LOCATION`** — `"Remote"`, or a city like `"New York"` / `"London"`.
- **`EXCLUDE_KEYWORDS`** — words that, if they appear in a job title, get it thrown out
  (e.g. drop `"manager"` if you only want individual-contributor roles).

If you skip this step, the scraper will happily fetch the wrong jobs. You've been warned. 🙂

---

## Prerequisites

You need these installed **before** running the pipeline:

| Requirement | Why | Where |
|---|---|---|
| **Python 3.10+** | Runs the pipeline | https://www.python.org/downloads/ |
| **Ollama** | Runs the local AI models | https://ollama.com/download |
| **MiKTeX** (Windows) or **TeX Live** (Mac/Linux) — provides `pdflatex` | Compiles résumés to PDF | https://miktex.org/download |

> After installing MiKTeX, make sure `pdflatex` is on your PATH (reopen your terminal).
> On its first compile MiKTeX may pop up asking to install LaTeX packages — let it.

---

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install the Playwright browser

Needed for the **Wellfound** source (it drives a headless browser). The other
sources don't need it, but installing it now is harmless and avoids a warning.

```bash
playwright install chromium
```

### 3. Pull the AI models

Make sure the Ollama app/server is running, then pull both models. **You run these yourself:**

```bash
ollama pull qwen2.5:3b
ollama pull qwen2.5:7b-instruct-q5_K_M
```

- `qwen2.5:3b` — fast, used for **scoring** all the jobs.
- `qwen2.5:7b-instruct-q5_K_M` — bigger and smarter, used for **tailoring** résumés.
  This is the quantized (`q5_K_M`) build, chosen so it fits comfortably on a 16GB / RTX 3060 machine.

> These exact model tags must match what's in [`config.py`](config.py)
> (`SCORING_MODEL` and `TAILORING_MODEL`). If you pull a different tag, update config to match.

### Sources status — read this so the results make sense

All five sources you asked for are wired up and enabled in `config.py`, but they
are **not equally reliable** (job boards actively fight scrapers, and some change
or gate their data). Verified live on 2026-06-30:

| Source | Status | Notes |
|---|---|---|
| **LinkedIn** | ✅ Works well | Uses LinkedIn's public guest endpoint (no login). The main workhorse. May occasionally rate-limit. |
| **Simplify** | ✅ Works | Pulls SimplifyJobs' public GitHub lists. These skew **new-grad / SWE / hardware**, so "design engineer" surfaces chip-design roles (FPGA/ASIC) too — the scorer ranks those low. |
| **YCombinator** | ⚠️ Limited | Scrapes YC's monthly "Ask HN: Who is hiring?" thread (Work at a Startup itself needs a login). Because the thread is **monthly** and the pipeline only keeps the last 24h by default, most of it gets filtered out mid-month — see the tip below. |
| **Indeed** | ❌ Effectively dead | Indeed **discontinued public RSS** (the feed returns HTTP 403). It's left enabled in case it's ever restored, but expect zero results from it. |
| **Wellfound** | ⚠️ Best-effort | Login-gated + anti-bot. Usually returns little or nothing without an authenticated session. Requires `playwright install chromium`. |

> 💡 **Tip — getting more from YCombinator:** if you want the YC/HN thread to
> contribute more, raise `LOOKBACK_HOURS` in `config.py` (e.g. `168` for a week,
> or `720` for the whole month). The trade-off is that LinkedIn/other sources will
> then include older postings too.

### 4. Add your master résumé

Drop your résumé as a LaTeX file here:

```
resume/master.tex
```

**This file is gitignored on purpose** — it contains your name, contact info, and work
history, and you don't want that committed to a public repo. The tailoring step reads this
file, rewrites the allowed sections, and writes new copies into `resume/output/`.

---

## Running the pipeline

Run these from the project root, in order:

```bash
# Step 1 — Scrape recent jobs, score them, and write results.xlsx (run once per session)
python main.py scrape

# Step 2 — Tailor résumés for the top 10 jobs (ranks 1–10)
python main.py batch --n 1

# Step 3 — Tailor the next 10 (ranks 11–20)
python main.py batch --n 2

# ...and so on (--n 3, --n 4) for lower-ranked jobs
```

Two optional helpers:

```bash
# Re-score the jobs you already scraped, without scraping again
python main.py score

# Show the current rankings in the terminal
python main.py status
```

---

## What output to expect

- **`data/results.xlsx`** — the master spreadsheet. New rows are *appended* every run
  (it never overwrites old data). Top-10 jobs are highlighted **green**; jobs you've
  tailored a résumé for are highlighted **blue**. Columns include score, reasons,
  missing skills, ATS keywords, status, and the path to the tailored PDF.
- **`resume/output/batch_1/`, `batch_2/`, …** — your tailored résumés, one `.tex` and one
  `.pdf` per job, named like `acme_corp_senior_product_designer.pdf`.
- **`data/jobs_raw.json`** / **`data/jobs_scored.json`** — the raw and scored job data
  (useful for debugging; safe to delete).
- **`pipeline.log`** — a log of every step and AI call, for when something looks off.

---

## Troubleshooting

**`pdflatex: command not found` / "pdflatex not found on PATH"**
LaTeX isn't installed or isn't on your PATH. Install MiKTeX (Windows) or TeX Live
(Mac/Linux), then **close and reopen your terminal** so the PATH updates. Résumés will
still be tailored to `.tex`; only the PDF step is skipped until this is fixed.

**`Ollama connection refused` / "Is the Ollama server running?"**
The Ollama server isn't running, or it's on a different address. Start the Ollama app
(or run `ollama serve`), confirm it's reachable at `http://localhost:11434`, and that
you've pulled both models (`ollama list` to check). If Ollama runs elsewhere, set
`OLLAMA_BASE_URL` in `config.py` (or `.env`).

**Empty or thin scrape results ("No jobs found" / fewer than expected)**
Usually one of:
- You haven't changed `SEARCH_KEYWORDS` / `LOCATION` in `config.py` (see the section near
  the top), so the search is too narrow or wrong.
- **The 24-hour window is cutting most results** — `LOOKBACK_HOURS` defaults to 24.
  LinkedIn postings older than a day and almost the entire YC/HN monthly thread get
  filtered out. Raise `LOOKBACK_HOURS` (e.g. `168` for a week) to capture more.
- **Don't expect anything from Indeed** — its public RSS is discontinued and returns
  HTTP 403 (you'll see a "malformed feed" warning in `pipeline.log`). This is normal;
  LinkedIn/Simplify/YC carry the load.
- **Wellfound returning nothing** is expected without a logged-in session (and you need
  `playwright install chromium` for it to even launch).
- Network/feed hiccup — check `pipeline.log`, which logs a per-source job count every run.

**"Master resume not found"**
You haven't put your résumé at `resume/master.tex` yet. See setup step 4.

**Tailoring produced invalid LaTeX / "compile error"**
The model occasionally returns LaTeX that won't compile. The pipeline retries once
automatically. If it still fails, open the generated `.tex` in `resume/output/batch_N/`
and check `pipeline.log` for the `pdflatex` error tail.

---

## Project layout

```
job-pipeline/
├── main.py            # CLI (scrape / batch / score / status)
├── config.py          # All your settings live here
├── requirements.txt
├── prompts/           # AI prompt templates (scoring + tailoring)
├── pipeline/          # scraper, scorer, tailor, compiler, excel, llm, utils
├── resume/            # master.tex (you add) + output/ (generated)
└── data/              # json + results.xlsx (generated)
```
