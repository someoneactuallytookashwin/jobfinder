"""Job application pipeline — CLI entrypoint.

Commands:
    python main.py scrape          # scrape + score all jobs, write Excel
    python main.py batch --n 1     # tailor + compile resumes for batch 1 (ranks 1-10)
    python main.py score           # re-score existing jobs_raw.json (no re-scrape)
    python main.py status          # print current rankings to the terminal

Requires a local Ollama server running with the configured models pulled.
See README.md.
"""

from __future__ import annotations

import os

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

import config
from pipeline import compiler, excel, scorer, scraper, tailor
from pipeline.utils import ensure_dirs, get_logger, load_json, save_json

load_dotenv()
console = Console()
logger = get_logger()


@click.group()
def cli() -> None:
    """Local, on-demand job application pipeline."""
    ensure_dirs()


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

@cli.command()
def scrape() -> None:
    """Scrape jobs, score the top candidates, and write results.xlsx."""
    console.rule("[bold cyan]Scrape & Score")

    with console.status("[cyan]Scraping enabled sources…", spinner="dots"):
        jobs = scraper.scrape_all()

    if not jobs:
        console.print(
            "[yellow]No jobs found.[/yellow] Check your SEARCH_KEYWORDS / LOCATION "
            "in [bold]config.py[/bold], your internet connection, and that at least "
            "one source is enabled in SOURCES."
        )
        return

    save_json(config.RAW_JSON_PATH, jobs)
    console.print(f"[green]Scraped {len(jobs)} job(s)[/green] → {config.RAW_JSON_PATH}")

    try:
        resume_content = tailor.load_master_resume()
    except FileNotFoundError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        console.print("Scored output will be skipped until a master resume exists.")
        return

    ranked = _score_with_progress(jobs, resume_content)
    save_json(config.SCORED_JSON_PATH, ranked)
    excel.write_results(ranked)

    console.print(
        f"[green]Scored & ranked {len(ranked)} job(s)[/green] → "
        f"{config.SCORED_JSON_PATH} and {config.EXCEL_PATH}"
    )
    _print_rankings(ranked, limit=15)


# ---------------------------------------------------------------------------
# score (re-score without re-scraping)
# ---------------------------------------------------------------------------

@cli.command()
def score() -> None:
    """Re-score the existing jobs_raw.json without scraping again."""
    console.rule("[bold cyan]Re-score")

    jobs = load_json(config.RAW_JSON_PATH, default=None)
    if not jobs:
        console.print(
            f"[yellow]No raw jobs at {config.RAW_JSON_PATH}.[/yellow] "
            "Run [bold]python main.py scrape[/bold] first."
        )
        return

    try:
        resume_content = tailor.load_master_resume()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    ranked = _score_with_progress(jobs, resume_content)
    save_json(config.SCORED_JSON_PATH, ranked)
    excel.write_results(ranked)
    console.print(f"[green]Re-scored {len(ranked)} job(s).[/green]")
    _print_rankings(ranked, limit=15)


# ---------------------------------------------------------------------------
# batch --n N
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--n", "batch_n", type=int, required=True, help="Batch number (1 = ranks 1-10).")
def batch(batch_n: int) -> None:
    """Tailor and compile resumes for the Nth batch of ranked jobs."""
    console.rule(f"[bold cyan]Tailor Batch {batch_n}")

    if batch_n < 1:
        console.print("[red]Batch number must be >= 1.[/red]")
        return

    jobs = load_json(config.SCORED_JSON_PATH, default=None)
    if not jobs:
        console.print(
            f"[yellow]No scored jobs at {config.SCORED_JSON_PATH}.[/yellow] "
            "Run [bold]python main.py scrape[/bold] first."
        )
        return

    start = (batch_n - 1) * config.BATCH_SIZE
    end = start + config.BATCH_SIZE
    batch_jobs = jobs[start:end]

    if not batch_jobs:
        console.print(
            f"[yellow]Batch {batch_n} is empty[/yellow] — only {len(jobs)} job(s) "
            f"available (batch size {config.BATCH_SIZE})."
        )
        return

    try:
        resume_latex = tailor.load_master_resume()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not compiler.pdflatex_available():
        console.print(
            "[yellow]Warning:[/yellow] pdflatex not found on PATH. Resumes will be "
            "tailored to .tex but PDF compilation will be skipped. Install MiKTeX / "
            "TeX Live to enable PDFs."
        )

    results: list[tuple[dict, str]] = []  # (job, outcome)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Tailoring resumes", total=len(batch_jobs))
        for job in batch_jobs:
            label = f"{job.get('company', '?')} — {job.get('title', '?')}"
            progress.update(task, description=f"Tailoring: {label[:40]}")

            tex_path = tailor.tailor_job(job, resume_latex, batch_n)
            if tex_path is None:
                results.append((job, "tailor failed"))
                progress.advance(task)
                continue

            outcome = "tailored (.tex)"
            resume_file = tex_path
            if compiler.pdflatex_available():
                out_dir = os.path.dirname(tex_path)
                if compiler.compile_tex(tex_path, out_dir):
                    resume_file = compiler.pdf_path_for(tex_path, out_dir)
                    outcome = "tailored + PDF"
                else:
                    outcome = "tailored, compile error"

            job["batch"] = batch_n
            job["status"] = "tailored"
            job["resume_file"] = resume_file
            excel.update_job_status(job["id"], status="tailored", resume_file=resume_file)
            results.append((job, outcome))
            progress.advance(task)

    # Persist batch/status back to the scored file.
    save_json(config.SCORED_JSON_PATH, jobs)
    _print_batch_summary(batch_n, results)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
def status() -> None:
    """Print the current rankings from jobs_scored.json."""
    console.rule("[bold cyan]Current Rankings")
    jobs = load_json(config.SCORED_JSON_PATH, default=None)
    if not jobs:
        console.print(
            f"[yellow]No scored jobs at {config.SCORED_JSON_PATH}.[/yellow] "
            "Run [bold]python main.py scrape[/bold] first."
        )
        return
    _print_rankings(jobs, limit=len(jobs))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_with_progress(jobs: list[dict], resume_content: str) -> list[dict]:
    n = min(len(scorer.prefilter(jobs)), config.TOP_N_TO_RANK)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scoring jobs", total=max(n, 1))

        def _cb(job: dict) -> None:
            progress.advance(task)
            progress.update(task, description=f"Scored: {job.get('title', '?')[:40]}")

        ranked = scorer.score_jobs(jobs, resume_content, progress_cb=_cb)
    return ranked


def _print_rankings(jobs: list[dict], limit: int = 15) -> None:
    table = Table(title="Job Rankings", show_lines=False, header_style="bold magenta")
    table.add_column("Rank", justify="right", style="cyan", no_wrap=True)
    table.add_column("Score", justify="right")
    table.add_column("Title", style="white")
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Status")

    for job in jobs[:limit]:
        score = job.get("score")
        score_str = str(score) if score is not None else "-"
        score_style = "green" if (score or 0) >= 7 else ("yellow" if (score or 0) >= 4 else "red")
        table.add_row(
            str(job.get("rank", "-")),
            f"[{score_style}]{score_str}[/{score_style}]",
            (job.get("title") or "")[:45],
            (job.get("company") or "")[:25],
            (job.get("location") or "")[:18],
            job.get("status", ""),
        )

    console.print(table)
    if len(jobs) > limit:
        console.print(f"[dim]…and {len(jobs) - limit} more. Run 'status' to see all.[/dim]")


def _print_batch_summary(batch_n: int, results: list[tuple[dict, str]]) -> None:
    table = Table(title=f"Batch {batch_n} Summary", header_style="bold magenta")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Outcome")

    for job, outcome in results:
        style = "green" if "PDF" in outcome else ("yellow" if "tailored" in outcome else "red")
        table.add_row(
            (job.get("company") or "")[:25],
            (job.get("title") or "")[:40],
            f"[{style}]{outcome}[/{style}]",
        )
    console.print(table)

    done = sum(1 for _, o in results if "tailored" in o)
    console.print(
        f"[green]Batch {batch_n} complete:[/green] {done}/{len(results)} tailored. "
        f"Output in [bold]{os.path.join(config.OUTPUT_DIR, f'batch_{batch_n}')}[/bold]."
    )


if __name__ == "__main__":
    cli()
