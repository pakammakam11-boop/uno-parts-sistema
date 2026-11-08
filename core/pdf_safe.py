"""Saugus PDF teksto nuskaitymas: pdfplumber veikia ATSKIRAME procese su ribomis.

Kodėl: nepatikimas PDF (pašto priedas) gali būti dekompresijos bomba (keli KB -> šimtai MB). Tėvinis procesas
(app / worker) dėl to negali nukristi, todėl parseris:
  * paleidžiamas vaiko procese su laiko riba (TIMEOUT_SECONDS), po kurios nužudomas;
  * Linux (konteineris): RLIMIT_AS atminties riba (MEMORY_MB); Windows: tik laiko riba;
  * nuskaito ne daugiau MAX_PAGES puslapių ir nutraukia, kai tekstas viršija MAX_TEXT_BYTES.
Pažeidimas -> PdfUnsafe("PDF per didelis arba sugadintas"); sąskaita virsta radiniu, o ne nelaimingu atsitikimu.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 30
MEMORY_MB = 384   # vienam PDF vaikui; kartu su PARSE_SLOTS telpa į 1 GiB konteinerį (Sol S7)
MAX_PAGES = 50
MAX_TEXT_BYTES = 2 * 1024 * 1024
MSG_UNSAFE = "PDF per didelis arba sugadintas"
_EXIT_TOO_MANY_PAGES, _EXIT_TOO_MUCH_TEXT = 4, 3
ROOT = Path(__file__).resolve().parent.parent


class PdfUnsafe(Exception):
    """PDF viršijo saugos ribas arba negalima jo perskaityti."""

    def __init__(self, message: str = MSG_UNSAFE):
        super().__init__(message)


def extract_text(path: Path | str) -> str:
    """Visų puslapių tekstas (kaip „\n“.join). Meta PdfUnsafe, kai pažeidžiamos ribos ar PDF sugadintas."""
    cmd = [sys.executable, "-m", "core.pdf_safe", str(path), str(MAX_PAGES), str(MAX_TEXT_BYTES), str(MEMORY_MB)]
    try:
        # stderr į DEVNULL: PDF bibliotekos perspėjimų srautas neribotas ir kauptųsi tėviniame procese (Sol S6)
        done = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as e:
        raise PdfUnsafe() from e
    if done.returncode != 0:
        raise PdfUnsafe()
    return done.stdout.decode("utf-8", errors="replace")


def _child(path: str, max_pages: int, max_text: int, memory_mb: int) -> int:
    if sys.platform != "win32" and memory_mb > 0:
        import resource
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    import pdfplumber

    parts: list[str] = []
    total = 0
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > max_pages:
            return _EXIT_TOO_MANY_PAGES
        for page in pdf.pages:
            text = page.extract_text() or ""
            total += len(text.encode("utf-8"))
            if total > max_text:
                return _EXIT_TOO_MUCH_TEXT
            parts.append(text)
    sys.stdout.buffer.write("\n".join(parts).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_child(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
    except MemoryError:
        sys.exit(5)
    except Exception:  # sugadintas PDF: tėvas paverčia radiniu
        sys.exit(1)
