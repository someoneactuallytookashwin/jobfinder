"""pdflatex compilation.

Compiles a .tex file to PDF, suppresses noisy output unless something breaks,
verifies the PDF actually appeared, and cleans up LaTeX aux files.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from pipeline.utils import get_logger

logger = get_logger()

_AUX_EXTENSIONS = (".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk")


def pdflatex_available() -> bool:
    """True if a `pdflatex` binary is on PATH."""
    return shutil.which("pdflatex") is not None


def compile_tex(tex_path: str, output_dir: str) -> bool:
    """Compile a .tex file to PDF with pdflatex.

    Returns True only if pdflatex exits 0 AND the expected .pdf exists.
    Aux files are cleaned up afterward regardless of outcome.
    """
    if not pdflatex_available():
        logger.error(
            "pdflatex not found on PATH. Install MiKTeX (Windows) or TeX Live "
            "and reopen your terminal."
        )
        return False

    os.makedirs(output_dir, exist_ok=True)
    logger.info("Compiling %s → %s", tex_path, output_dir)

    try:
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-output-directory",
                output_dir,
                tex_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.error("pdflatex timed out compiling %s", tex_path)
        _cleanup_aux(tex_path, output_dir)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("pdflatex failed to run on %s: %s", tex_path, exc)
        return False

    pdf_path = _expected_pdf_path(tex_path, output_dir)
    ok = result.returncode == 0 and os.path.exists(pdf_path)

    if not ok:
        # Only surface the noisy log when something actually went wrong.
        logger.error(
            "Compile failed for %s (returncode=%s, pdf_exists=%s).\n--- pdflatex stdout tail ---\n%s",
            tex_path, result.returncode, os.path.exists(pdf_path),
            (result.stdout or "")[-1500:],
        )
    else:
        logger.info("Compiled PDF: %s", pdf_path)

    _cleanup_aux(tex_path, output_dir)
    return ok


def _expected_pdf_path(tex_path: str, output_dir: str) -> str:
    base = os.path.splitext(os.path.basename(tex_path))[0]
    return os.path.join(output_dir, base + ".pdf")


def pdf_path_for(tex_path: str, output_dir: str) -> str:
    """Public helper: the PDF path that compile_tex would produce."""
    return _expected_pdf_path(tex_path, output_dir)


def _cleanup_aux(tex_path: str, output_dir: str) -> None:
    """Delete LaTeX auxiliary files produced during compilation."""
    base = os.path.splitext(os.path.basename(tex_path))[0]
    for ext in _AUX_EXTENSIONS:
        candidate = os.path.join(output_dir, base + ext)
        if os.path.exists(candidate):
            try:
                os.remove(candidate)
            except OSError as exc:
                logger.warning("Could not remove aux file %s: %s", candidate, exc)
