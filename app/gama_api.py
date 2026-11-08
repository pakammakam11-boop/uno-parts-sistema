"""GAMA mokesčių patikra iš /app: snapshot'as iš Rivilė GAMA (tools/gama_snapshot.ps1) -> core.gama_check.

Konfigūracija (aplinkos kintamieji, skaitomi kiekvienos užklausos metu):
  UNO_GAMA_SNAPSHOT_CMD  komanda snapshot'ui; JSON sąrašas (["py","fake.py"]) arba eilutė.
                         Numatyta: powershell -NoProfile -ExecutionPolicy Bypass -File <repo>/tools/gama_snapshot.ps1
                         Prie jos visada pridedama: -Month YYYY-MM -OutDir <įmonės katalogas>.
  UNO_GAMA_DATA_DIR      realių duomenų katalogas (numatyta %USERPROFILE%\\UNO_DATA); repo viduje draudžiama.
  UNO_GAMA_COMPANIES     įmonių ID, kurioms leidžiama GAMA patikra (numatyta "1"; GAMA yra viena UNO Parts DB).

Kiekvienos įmonės failai laikomi <UNO_GAMA_DATA_DIR>/company_<id>/ (gama_<mėnuo>.json ir XLSX ataskaitos).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import auth
from app.api_v1 import READERS, WRITERS, XLSX_TYPE, _plain, get_store
from core.gama import load_snapshot
from core.gama_check import check_month
from core.store import Store
from tools.gama_check import write_report

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT_TIMEOUT_S = 300
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

MSG_BAD_MONTH = "Netaisyklingas mėnuo: tikimasi YYYY-MM"
MSG_BUSY = "GAMA patikra jau vykdoma, palaukite, kol ji baigsis"
MSG_DATA_IN_REPO = "GAMA duomenų katalogas negali būti programos kataloge: nustatykite UNO_GAMA_DATA_DIR už jo ribų"
MSG_NOT_CONFIGURED = "GAMA patikra šiai įmonei nesukonfigūruota"
MSG_TIMEOUT = "GAMA neatsakė per 5 min. Bandykite vėliau arba kreipkitės į administratorių"
MSG_START_FAIL = "Nepavyko paleisti GAMA eksporto: kreipkitės į administratorių"
MSG_EXPORT_FAIL = "GAMA eksportas nepavyko"
MSG_NO_FILE = "GAMA eksportas baigėsi, bet duomenų failo nėra"
MSG_BAD_SNAPSHOT = "GAMA duomenų failas sugadintas: paleiskite su „Atnaujinti iš GAMA“"

log = logging.getLogger("uno.gama")
router = APIRouter(prefix="/api/v1/gama")
_lock = threading.Lock()  # vienas procesas: viena GAMA patikra vienu metu


class CheckBody(BaseModel):
    month: str
    refresh: bool = True


# ---------- konfigūracija ----------

def data_dir() -> Path:
    raw = os.environ.get("UNO_GAMA_DATA_DIR") or str(Path(os.environ.get("USERPROFILE") or Path.home()) / "UNO_DATA")
    path = Path(raw).resolve()
    if path == REPO or REPO in path.parents:
        log.error("UNO_GAMA_DATA_DIR repo viduje: %s", path)
        raise HTTPException(500, MSG_DATA_IN_REPO)
    return path


def company_dir(company_id: int) -> Path:
    return data_dir() / f"company_{int(company_id)}"


def allowed_companies() -> set[int]:
    raw = os.environ.get("UNO_GAMA_COMPANIES", "1")
    return {int(x) for x in re.split(r"[,\s]+", raw.strip()) if x.isdigit()}


def snapshot_command() -> list[str]:
    raw = (os.environ.get("UNO_GAMA_SNAPSHOT_CMD") or "").strip()
    if not raw:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(REPO / "tools" / "gama_snapshot.ps1")]
    if raw.startswith("["):
        return [str(x) for x in json.loads(raw)]
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t for t in shlex.split(raw, posix=False)]


def require_company(user: auth.User) -> int:
    if user.company_id not in allowed_companies():
        raise HTTPException(403, MSG_NOT_CONFIGURED)
    return user.company_id


# ---------- snapshot ----------

def run_snapshot(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Atskira funkcija, kad testai galėtų ją pakeisti (monkeypatch). Be shell=True."""
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, shell=False)


def _first_line(text: str) -> str:
    line = next((x.strip() for x in (text or "").splitlines() if x.strip()), "")
    return line[:200]


def refresh_snapshot(month: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [*snapshot_command(), "-Month", month, "-OutDir", str(out_dir)]
    try:
        proc = run_snapshot(cmd, SNAPSHOT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.error("GAMA snapshot %s: timeout %s s", month, SNAPSHOT_TIMEOUT_S)
        raise HTTPException(502, MSG_TIMEOUT) from None
    except OSError:
        log.exception("GAMA snapshot %s: nepavyko paleisti", month)
        raise HTTPException(502, MSG_START_FAIL) from None
    if proc.returncode != 0:
        log.error("GAMA snapshot %s: kodas %s, stderr: %s", month, proc.returncode, proc.stderr)
        first = _first_line(proc.stderr)
        raise HTTPException(502, f"{MSG_EXPORT_FAIL}: {first}" if first else MSG_EXPORT_FAIL)


def _serialize(findings) -> list[dict]:
    plain = [_plain(f) for f in findings]
    return sorted(plain, key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))


def _execute(cid: int, month: str, refresh: bool, username: str, store: Store) -> dict:
    out_dir = company_dir(cid)
    snap = out_dir / f"gama_{month}.json"
    if refresh or not snap.is_file():
        refresh_snapshot(month, out_dir)
    if not snap.is_file():
        raise HTTPException(502, MSG_NO_FILE)
    raw = snap.read_bytes()
    try:
        snapshot = load_snapshot(snap)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        log.exception("GAMA snapshot %s sugadintas", snap)
        raise HTTPException(422, MSG_BAD_SNAPSHOT) from None
    try:
        findings, summary = check_month(snapshot, month)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    xlsx_name = f"gama_check_{month}_{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.xlsx"
    write_report(out_dir / xlsx_name, findings, summary)
    plain = _serialize(findings)
    run_id = store.add_gama_run(cid, month, username, hashlib.sha256(raw).hexdigest(), summary, plain, xlsx_name)
    counts = summary.get("findings", {})
    store.log(cid, username, "run", f"gama_check {month} #{run_id} klaidos {counts.get('error', 0)}, "
              f"įspėjimai {counts.get('warning', 0)}{' (atnaujinta iš GAMA)' if refresh else ''}", month)
    return _run_dict(store.get_gama_run(cid, run_id))


def _run_dict(run: dict) -> dict:
    out = {"run_id": run["id"], "month": run["month"], "created_at": run["created_at"],
           "created_by": run["created_by"], "summary": run["summary"], "xlsx_name": run["xlsx_name"]}
    if "findings" in run:
        out["findings"] = run["findings"]
    return out


# ---------- maršrutai ----------

@router.post("/check")
def gama_check(body: CheckBody, store: Store = Depends(get_store),
               user: auth.User = Depends(auth.require_role(*WRITERS))):
    cid = require_company(user)
    if not MONTH_RE.fullmatch(body.month or ""):
        raise HTTPException(400, MSG_BAD_MONTH)
    data_dir()  # klaidinga konfigūracija -> klaida prieš bet kokį darbą
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, MSG_BUSY)
    try:
        return _execute(cid, body.month, body.refresh, user.username, store)
    finally:
        _lock.release()


@router.get("/runs")
def gama_runs(month: str | None = None, store: Store = Depends(get_store),
              user: auth.User = Depends(auth.require_role(*READERS))):
    cid = require_company(user)
    if month and not MONTH_RE.fullmatch(month):
        raise HTTPException(400, MSG_BAD_MONTH)
    return {"runs": [_run_dict(r) for r in store.list_gama_runs(cid, month)]}


def _get_run(store: Store, cid: int, run_id: int) -> dict:
    run = store.get_gama_run(cid, run_id)
    if run is None:
        raise HTTPException(404, "GAMA patikra nerasta")
    return run


@router.get("/runs/{run_id}")
def gama_run(run_id: int, store: Store = Depends(get_store),
             user: auth.User = Depends(auth.require_role(*READERS))):
    return _run_dict(_get_run(store, require_company(user), run_id))


@router.get("/runs/{run_id}/xlsx")
def gama_run_xlsx(run_id: int, store: Store = Depends(get_store),
                  user: auth.User = Depends(auth.require_role(*READERS))):
    cid = require_company(user)
    run = _get_run(store, cid, run_id)
    base = company_dir(cid)
    path = (base / run["xlsx_name"]).resolve()
    if base.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Ataskaitos failo nebėra")
    store.log(cid, user.username, "export", f"gama_check {run['month']} #{run_id} XLSX", run["month"])
    return FileResponse(path, media_type=XLSX_TYPE, filename=path.name)
