"""Pašto importas be naršyklės: tas pats, ką daro mygtukas „Importuoti iš pašto“ (services.run_invoices_mail):
IMAP parsisiuntimas, sąskaitų patikra, AI laukai (UNO_MAIL_AI, UNO_AI_PROVIDER), AI talpykla.

  py -3.12 -m tools.mail_poll                                # vienkartinis paleidimas
  py -3.12 -m tools.mail_poll --loop --interval-minutes 15   # docker worker
  py -3.12 -m tools.mail_poll --healthcheck                  # 0 = paskutinis bandymas šviežias ir ne nuolat klystantis

Išėjimo kodai: 0 sėkmė, 1 klaida (pašto / apdorojimo), 2 paštas nesukonfigūruotas.
Būsena: <UNO_MAIL_DIR>_status.json (app ją skaito, kad naujos sąskaitos atsirastų Sąskaitų skiltyje).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone

from app import services
from core import mail_import

log = logging.getLogger("uno.mail_poll")
EXIT_OK, EXIT_FAIL, EXIT_NOT_CONFIGURED = 0, 1, 2
DEFAULT_INTERVAL_MIN = 15
DEFAULT_DAYS = 30
MAX_FAILURES_HEALTHY = 3
HEALTH_SLACK_MIN = 5
KEPT_ON_FAILURE = ("pdf_count", "parsed", "errors", "warnings")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_status(status: dict) -> None:
    path = services.mail_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _summary(result: dict, fetched: int) -> dict:
    counts = services.counts(result["_findings"])
    parsed = next((k["value"] for k in result["kpis"] if k["label"] == "Nuskaityta sąskaitų"), "0")
    return {"pdf_count": fetched, "parsed": int(parsed), "errors": counts["error"], "warnings": counts["warning"]}


def _import(days: int) -> dict:
    with services.mail_import_lock():  # tas pats užraktas kaip rankiniame mygtuke
        fetched = len(services.download_mail_pdfs(days))
        return _summary(services.build_mail_result(fetched, days), fetched)


def poll_once(days: int = DEFAULT_DAYS) -> tuple[int, dict]:
    """Vienas importas. Grąžina (išėjimo kodas, būsena); būsena įrašoma į failą."""
    previous = services.read_mail_status() or {}
    status: dict = {"started_at": _now(), "ok": False}
    code = EXIT_OK
    try:
        status.update(_import(days), ok=True)
        log.info("Pašto importas: %(pdf_count)s PDF, %(parsed)s nuskaityta, klaidų %(errors)s, įspėjimų %(warnings)s", status)
    except mail_import.MailNotConfigured as exc:
        status["error"], code = str(exc), EXIT_NOT_CONFIGURED
        log.error("Paštas nesukonfigūruotas: %s", exc)
    except mail_import.MailError as exc:
        status["error"], code = str(exc), EXIT_FAIL
        log.error("Pašto klaida: %s", exc)
    except Exception as exc:  # apdorojimo klaida: žurnale be sąskaitų turinio
        status["error"], code = f"Apdorojimo klaida ({type(exc).__name__})", EXIT_FAIL
        log.error("Pašto importo apdorojimas nepavyko: %s", type(exc).__name__)  # be teksto / sąskaitos turinio
    status["finished_at"] = _now()
    if status["ok"]:
        status["consecutive_failures"] = 0
        status["last_ok_at"] = status["finished_at"]
    else:
        status["consecutive_failures"] = int(previous.get("consecutive_failures", 0)) + 1
        if previous.get("last_ok_at"):
            status["last_ok_at"] = previous["last_ok_at"]
        for key in KEPT_ON_FAILURE:  # paskutinis sėkmingas rezultatas lieka matomas
            if key in previous:
                status[key] = previous[key]
    _write_status(status)
    return code, status


def run_loop(interval_minutes: int, days: int, stop: threading.Event | None = None) -> int:
    stop = stop or threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("Pašto procesas paleistas: kas %s min., paskutinės %s d.", interval_minutes, days)
    while not stop.is_set():
        poll_once(days)  # klaida nestabdo ciklo: kita iteracija bando iš naujo
        stop.wait(interval_minutes * 60)
    log.info("Pašto procesas sustabdytas")
    return EXIT_OK


def healthy(interval_minutes: int, now: datetime | None = None) -> bool:
    status = services.read_mail_status()
    if not status or not status.get("finished_at"):
        return False
    try:
        finished = datetime.fromisoformat(status["finished_at"])
    except ValueError:
        return False
    age_min = ((now or datetime.now(timezone.utc)) - finished).total_seconds() / 60
    fresh = age_min <= interval_minutes * 2 + HEALTH_SLACK_MIN
    return fresh and int(status.get("consecutive_failures", 0)) < MAX_FAILURES_HEALTHY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UNO pašto importas (sąskaitos iš Gmail)")
    parser.add_argument("--loop", action="store_true", help="kartoti kas --interval-minutes (docker worker)")
    parser.add_argument("--interval-minutes", type=int,
                        default=int(os.environ.get("MAIL_POLL_MINUTES") or DEFAULT_INTERVAL_MIN))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="kiek dienų atgal žiūrėti laiškus")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.interval_minutes < 1:
        parser.error("--interval-minutes turi būti >= 1")
    if args.healthcheck:
        return EXIT_OK if healthy(args.interval_minutes) else EXIT_FAIL
    if args.loop:
        return run_loop(args.interval_minutes, args.days)
    return poll_once(args.days)[0]


if __name__ == "__main__":
    sys.exit(main())
