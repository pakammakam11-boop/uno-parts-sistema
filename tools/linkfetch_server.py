"""Nuorodų sąskaitų atsisiuntimo paslauga (docker `linkfetch`): vienintelė vieta, kur veikia Chromium.

  python -m tools.linkfetch_server

POST /fetch  {"url": "https://..."}  + Authorization: Bearer <LINKFETCH_TOKEN>
  200 application/pdf  -> PDF baitai
  4xx/5xx application/json {"error": "..."}  -> priežastis lietuviškai (be URL)
GET /healthz -> 200 (be autentifikacijos, tik gyvumui)

Saugumo modelis (kodėl taip):
- Konteineris neturi jokių paslapčių (nei DB, nei IMAP, nei Google), tik LINKFETCH_TOKEN, ir yra tik `egress`
  (internetas) ir `fetchnet` (tik worker/app) tinkluose. Atmintį, procesų skaičių ir CPU riboja konteinerio cgroup
  (mem_limit / pids_limit / cpus), todėl atskiro RAM limito Python viduje nereikia.
- Vienu metu apdorojamas VIENAS užklausimas (užraktas); kitas gauna 503.
- Watchdog: bandymas, užtrukęs ilgiau nei WATCHDOG_S, nutraukiamas `os._exit(70)`. Pagrindinis procesas (PID 1 po tini)
  išeina, konteineris sustoja ir Docker (`restart: unless-stopped`) jį paleidžia iš naujo: žūsta VISI palikuonys,
  įskaitant atsiskyrusius Chromium procesus ir našlaičius. Tai yra S11/S14 garantija; jokių PID sąrašų nereikia.
- Išėjimas iš konteinerio tik per core.link_safety.EgressProxy (leidžiami domenai, viešieji IP, 443).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import link_invoices, link_safety

log = logging.getLogger("uno.linkfetch")
WATCHDOG_S = link_invoices.LINK_TIMEOUT_S
EXIT_WATCHDOG = 70
MAX_BODY = 4096
MIN_TOKEN_LEN = 16
_busy = threading.Lock()


def _domains() -> list[str]:
    return link_invoices.parse_domains(os.environ.get("UNO_INVOICE_LINK_DOMAINS") or None)


def _clamp(value, default: float, ceiling: float) -> float:
    """Kliento prašomas limitas gali tik SUMAŽINTI serverio limitą."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number <= 0 else min(number, ceiling)


def run_attempt(url: str, domains: list[str], max_seconds=None, max_bytes=None) -> tuple[bytes | None, str, int]:
    """Vienas bandymas šiame procese (tik laikant užraktą ir su watchdog). Grąžina (PDF, priežastis, sunaudoti baitai);
    galioja min(savo limitas, kliento duotas)."""
    canonical = link_safety.canonicalize(url, domains)
    if not canonical:
        return None, "nuoroda neleistina", 0
    soft = _clamp(max_seconds, link_invoices.CHILD_SOFT_DEADLINE_S, link_invoices.CHILD_SOFT_DEADLINE_S)
    budget = int(_clamp(max_bytes, link_invoices.ATTEMPT_BYTES, link_invoices.ATTEMPT_BYTES))
    tmp = tempfile.mkdtemp(prefix="uno-link-")
    try:
        os.makedirs(os.path.join(tmp, "dl"), exist_ok=True)
        data, reason, used = link_invoices._attempt(canonical, domains, tmp, budget, soft)
        return data, reason, used
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _watchdog_kill(_exit=os._exit) -> None:
    log.error("Bandymas viršijo %s s: konteineris sustabdomas (Docker jį paleis iš naujo)", WATCHDOG_S)
    _exit(EXIT_WATCHDOG)


class Handler(BaseHTTPRequestHandler):
    server_version = "linkfetch"
    token = ""

    def log_message(self, fmt, *args) -> None:  # numatytas žurnalas rašytų adresus
        return

    def _send(self, status: int, body: bytes, ctype: str, used: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if used is not None:
            self.send_header("X-Bytes-Used", str(used))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, used: int = 0) -> None:
        body = json.dumps({"error": message, "bytes_used": used}, ensure_ascii=False).encode()
        self._send(status, body, "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._error(404, "nerasta")

    def do_POST(self) -> None:  # noqa: N802
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {self.token}".encode()):
            return self._error(401, "neautorizuota")
        if self.path != "/fetch":
            return self._error(404, "nerasta")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError
            request = json.loads(self.rfile.read(length))
            url = request["url"]
            max_seconds, max_bytes = request.get("max_seconds"), request.get("max_bytes")
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._error(400, "bloga užklausa")
        if not _busy.acquire(timeout=2):
            return self._error(503, "paslauga užimta")
        timer = threading.Timer(WATCHDOG_S, _watchdog_kill)
        timer.daemon = True
        timer.start()
        try:
            data, reason, used = run_attempt(url, _domains(), max_seconds, max_bytes)
        except Exception as exc:
            data, reason, used = None, f"nuorodos apdoroti nepavyko ({type(exc).__name__})", 0
        finally:
            timer.cancel()
            _busy.release()
        if data:
            self._send(200, data, "application/pdf", used)
        else:
            self._error(422, reason or "PDF negautas", used)


def make_server(token: str, host: str = "0.0.0.0", port: int = 8099) -> ThreadingHTTPServer:
    if len(token) < MIN_TOKEN_LEN:
        raise SystemExit("LINKFETCH_TOKEN nenustatytas arba per trumpas")
    handler = type("BoundHandler", (Handler,), {"token": token})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    server = make_server(os.environ.get("LINKFETCH_TOKEN", ""), port=int(os.environ.get("LINKFETCH_PORT", "8099")))
    log.info("linkfetch klauso %s:%s", *server.server_address)
    server.serve_forever()


if __name__ == "__main__":
    main()
