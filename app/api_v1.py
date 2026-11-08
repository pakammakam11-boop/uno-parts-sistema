"""Gamybinis API 1 etapui: DU ir tabelio kontrolė (Rivilė skaičiuoja DU, mes tikriname tabelį ir palyginame).

Visi maršrutai reikalauja prisijungimo; POST dar tikrina CSRF ir rolę (accountant / admin).
company_id VISADA imamas iš prisijungusio naudotojo, niekada iš užklausos.
Saugykla: core.store.Store; kelias iš `app.state.uno_store` (testai) arba auth.db_path() (UNO_DB, kitaip data/uno.db).
"""
from __future__ import annotations

import dataclasses
import io
import json
import logging
import tempfile
import zipfile
from collections import Counter
from decimal import Decimal as D
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from app import auth
from core import approval, payroll_rules, rivile_payroll, timesheet
from core.employees import EmployeeRecord, EmployeesError, parse_employees
from core.models import Finding, money
from core.period import Period, lt_holidays
from core.rivile_payroll import PAY_FIELDS, RivilePayRow
from core.store import Store, to_json

RULES_VERSION = "2026.2"  # keisti, kai pasikeičia skaičiavimo taisyklės (kalendorius, šventės, liga)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_KINDS = {"employees": ".xlsx", "timesheet": ".xlsx", "rivile_hours": ".xlsx",
                "sick_certs": ".csv", "vacation_requests": ".csv", "rivile_payroll": ".xlsx"}
OPTIONAL_KINDS = ("rivile_hours", "sick_certs", "vacation_requests")
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WRITERS = ("accountant", "admin")
APPROVERS = ("accountant", "admin", "manager")
READERS = ("accountant", "admin", "manager")

MSG_NO_RUN = "Pirma paleiskite tabelio patikrą"
MSG_NOT_APPROVED = "Tabelis nepatvirtintas arba po patvirtinimo pasikeitė duomenys: pirma patvirtinkite tabelį"
MSG_STALE_RUN = "Po paskutinės patikros įkelta naujų failų arba pasikeitė taisyklės: paleiskite patikrą iš naujo"
KIND_LABELS = {"employees": "darbuotojų failas", "timesheet": "tabelis", "rivile_hours": "Rivilės valandos",
               "sick_certs": "ligos pažymos", "vacation_requests": "atostogų prašymai",
               "rivile_payroll": "Rivilės DU eksportas"}
MSG_GENERIC_FAIL = "Nepavyko apdoroti: {what}. Patikrinkite failų formatą; jei klaida kartojasi, kreipkitės į administratorių"

log = logging.getLogger("uno.api")

router = APIRouter(prefix="/api/v1")
_stores: dict[str, Store] = {}


# ---------- priklausomybės ir pagalbinės ----------

def get_store(request: Request) -> Store:
    override = getattr(request.app.state, "uno_store", None)
    if override is not None:
        return override
    path = str(auth.db_path())
    if path not in _stores:
        _stores[path] = Store(path)
    return _stores[path]


def get_period(period: str) -> Period:
    try:
        return Period.parse(period)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _plain(value):
    """Decimal -> eilutė, dataclass -> dict (JSON be float paklaidų)."""
    return json.loads(to_json(dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value))


def _latest(store: Store, company_id: int, period: Period) -> dict[str, dict]:
    """Naujausias kiekvieno tipo įkėlimas šiam laikotarpiui."""
    return {u["kind"]: u for u in store.list_uploads(company_id, str(period))}  # id didėja, paskutinis laimi


def _counts(findings: list[dict]) -> dict[str, int]:
    c = Counter(f["severity"] for f in findings)
    return {s: c.get(s, 0) for s in ("error", "warning", "info")}


def _guard(action, what: str):
    """Sugadinti / netinkami failai -> 422 su lietuvišku paaiškinimu, o ne 500.

    Žinomos validacijos klaidos (EmployeesError, ValueError iš parserių) jau parašytos naudotojui;
    kitų klaidų tekstas lieka tik serverio žurnale.
    """
    try:
        return action()
    except EmployeesError as exc:
        raise HTTPException(422, {"message": "Darbuotojų failas netinkamas", "errors": exc.errors}) from None
    except HTTPException:
        raise
    except ValueError as exc:
        log.warning("Validacijos klaida (%s): %s", what, exc)
        raise HTTPException(422, f"{MSG_GENERIC_FAIL.format(what=what)}. Priežastis: {exc}") from None
    except Exception:
        log.exception("Netikėta klaida (%s)", what)
        raise HTTPException(422, MSG_GENERIC_FAIL.format(what=what)) from None


# ---------- būsena ----------

def stale_reasons(store: Store, cid: int, period: Period, module: str, run: dict) -> list[str]:
    """Lietuviškos priežastys, kodėl išsaugotas skaičiavimas nebegalioja (tuščias sąrašas = aktualus)."""
    reasons = []
    if run["rules_version"] != RULES_VERSION:
        reasons.append(f"Pasikeitė skaičiavimo taisyklės (skaičiuota pagal {run['rules_version']}, "
                       f"dabar {RULES_VERSION}): paleiskite iš naujo")
    try:
        if module == "timesheet":
            for kind in approval.changed_input_kinds(store, cid, str(period), module, run["input_upload_ids"]):
                reasons.append(f"Po patikros įkeltas naujas failas: {KIND_LABELS.get(kind, kind)}")
        else:
            reasons += _compare_stale_reasons(store, cid, period, run)
    except KeyError:
        reasons.append("Skaičiavimo įvesties failų nebėra: paleiskite iš naujo")
    return reasons


def _compare_stale_reasons(store: Store, cid: int, period: Period, run: dict) -> list[str]:
    reasons = []
    ts_run_id = run["result"].get("timesheet_run_id")
    active = store.active_approval(cid, str(period), "timesheet")
    if (active is None or active["run_id"] != ts_run_id
            or not approval.is_approved(store, cid, str(period), "timesheet", RULES_VERSION)):
        reasons.append("Tabelio skaičiavimas, kuriuo remtasi palyginimas, nebepatvirtintas arba nebeaktualus")
    used = [u for u in (store.get_upload(cid, i) for i in run["input_upload_ids"]) if u]
    used_payroll = max((u["id"] for u in used if u["kind"] == "rivile_payroll"), default=None)
    latest = max((u["id"] for u in store.list_uploads(cid, str(period), "rivile_payroll")), default=None)
    if used_payroll != latest:
        reasons.append("Po palyginimo įkeltas naujas Rivilės DU eksportas")
    return reasons


def _run_summary(store: Store, cid: int, period: Period, module: str, run: dict | None) -> dict | None:
    if run is None:
        return None
    reasons = stale_reasons(store, cid, period, module, run)
    return {"id": run["id"], "created_at": run["created_at"], "rules_version": run["rules_version"],
            "counts": run["result"].get("counts", {}), "input_upload_ids": run["input_upload_ids"],
            "is_current": not reasons, "stale_reasons": reasons}


def _upload_summary(u: dict) -> dict:
    return {k: u[k] for k in ("id", "filename", "sha256", "uploaded_by", "uploaded_at")}


@router.get("/periods/{period}/state")
def period_state(period: Period = Depends(get_period), store: Store = Depends(get_store),
                 user: auth.User = Depends(auth.current_user)):
    cid = user.company_id
    uploads = {kind: [_upload_summary(u) for u in store.list_uploads(cid, str(period), kind)]
               for kind in UPLOAD_KINDS}
    approved = approval.is_approved(store, cid, str(period), "timesheet", RULES_VERSION)
    active = store.active_approval(cid, str(period), "timesheet") if approved else None
    return {
        "period": str(period), "label": period.label_lt,
        "uploads": {k: {"count": len(v), "latest": v[-1] if v else None} for k, v in uploads.items()},
        "runs": {m: _run_summary(store, cid, period, m, store.latest_run(cid, str(period), m))
                 for m in ("timesheet", "payroll_compare")},
        "approval": {"is_approved": approved,
                     "approved_by": active["approved_by"] if active else None,
                     "approved_at": active["approved_at"] if active else None,
                     "run_id": active["run_id"] if active else None},
    }


# ---------- įkėlimai ----------

def _validate_upload(kind: str, content: bytes, period: Period) -> None:
    """Darbuotojų ir tabelio failus patikriname iškart, kad klaida būtų matoma įkeliant."""
    if kind not in ("employees", "timesheet"):
        return
    with tempfile.TemporaryDirectory(prefix="uno_check_") as tmp:
        path = Path(tmp) / f"{kind}.xlsx"
        path.write_bytes(content)
        if kind == "employees":
            parse_employees(path)
        else:
            timesheet.load_timesheet(path, (period.year, period.month))


@router.post("/periods/{period}/uploads/{kind}")
def upload(kind: str, file: UploadFile = File(...), period: Period = Depends(get_period),
           store: Store = Depends(get_store), user: auth.User = Depends(auth.require_role(*WRITERS))):
    if kind not in UPLOAD_KINDS:
        raise HTTPException(404, f"Nežinomas failo tipas: {kind}")
    name = Path(file.filename or "").name
    if not name.lower().endswith(UPLOAD_KINDS[kind]):
        raise HTTPException(400, f"Tinka tik {UPLOAD_KINDS[kind]} failai: {name or '(be pavadinimo)'}")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Failas per didelis (daugiausia {MAX_UPLOAD_BYTES // 1024 // 1024} MB): {name}")
    if not content:
        raise HTTPException(400, f"Failas tuščias: {name}")
    _guard(lambda: _validate_upload(kind, content, period), name)
    try:
        saved = store.save_upload(user.company_id, str(period), kind, name, content, user.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"upload": _upload_summary(saved), "kind": kind, "period": str(period)}


# ---------- tabelio patikra ----------

def _rules_overtime_findings(rows: list[timesheet.TimesheetRow]) -> list[Finding]:
    """payroll_rules viršvalandžių ribos; kas jau virš 12 val. pažymėta tabelio patikroje, nekartojama."""
    out = []
    for r in rows:
        for f in payroll_rules.check_overtime_limits(r.tab_no, r.overtime_by_week):
            week = f.extra.get("week")
            if week is not None:
                if r.overtime_by_week[week - 1] > timesheet.OVERTIME_WEEK_LIMIT:
                    continue
                f = dataclasses.replace(
                    f, severity="warning",
                    detail=f.detail + " Daugiau nei 8 val. galima tik turint darbuotojo sutikimą (iki 12 val.).")
            out.append(f)
    return out


def _master_findings(rows, master: dict[str, EmployeeRecord]) -> list[Finding]:
    out = []
    in_sheet = {r.tab_no for r in rows}
    for r in rows:
        if r.tab_no not in master:
            out.append(Finding("timesheet", "error", "Darbuotojo nėra darbuotojų sąraše",
                               f"{r.tab_no} {r.name}: yra tabelyje, bet nėra darbuotojų faile.", ref=r.tab_no,
                               suggested_action="Apskaitininke, papildykite darbuotojų sąrašą.",
                               extra={"kind": "not_in_master"}))
    for tab, rec in master.items():
        if tab not in in_sheet:
            out.append(Finding("timesheet", "warning", "Darbuotojo nėra tabelyje",
                               f"{tab} {rec.name}: yra darbuotojų faile, bet nėra tabelyje.", ref=tab,
                               suggested_action="Patikrinkite, ar darbuotojas šį mėnesį dirbo.",
                               extra={"kind": "not_in_timesheet"}))
    return out


def _expected_summary(rec: EmployeeRecord, row, period: Period) -> dict:
    exp = payroll_rules.compute_expected(rec.terms(), row, None, period)
    data = _plain(exp)
    data.update(tab_no=rec.tab_no, name=rec.name, gross=str(exp.gross_total))
    return data


def _expected_all(rows, master: dict[str, EmployeeRecord], period: Period) -> dict[str, dict]:
    out = {}
    for r in rows:
        rec = master.get(r.tab_no)
        if rec is not None:
            out[r.tab_no] = _expected_summary(rec, r, period)
    return out


def _unsupported(rows, master, expected: dict[str, dict]) -> list[dict]:
    out = [{"tab_no": t, "name": e["name"], "reasons": e["unsupported_reasons"]}
           for t, e in expected.items() if not e["supported"]]
    out += [{"tab_no": r.tab_no, "name": r.name, "reasons": ["Nėra darbuotojų sąraše: DU neskaičiuojamas"]}
            for r in rows if r.tab_no not in master]
    return out


def _run_check(store: Store, cid: int, period: Period, uploads: dict[str, dict]) -> dict:
    path = lambda kind: uploads[kind]["stored_path"] if kind in uploads else None  # noqa: E731
    master = {e.tab_no: e for e in parse_employees(path("employees"))}
    workdays = period.workdays(lt_holidays(period.year))
    rows, findings = timesheet.check(
        path("timesheet"), path("rivile_hours"), path("sick_certs"), path("vacation_requests"),
        ym=(period.year, period.month), norms={t: e.hours_norm for t, e in master.items()}, workdays=workdays)
    findings = (findings + _rules_overtime_findings(rows) + _master_findings(rows, master)
                + [payroll_rules.annual_overtime_not_performed_finding()])  # metų istorijos dar neturime
    expected = _expected_all(rows, master, period)
    plain = [_plain(f) for f in findings]
    return {"period": str(period), "workdays": workdays, "employees": len(master), "rows": len(rows),
            "counts": _counts(plain), "findings": plain, "expected": expected,
            "unsupported": _unsupported(rows, master, expected)}


@router.post("/periods/{period}/check")
def check(period: Period = Depends(get_period), store: Store = Depends(get_store),
          user: auth.User = Depends(auth.require_role(*WRITERS))):
    uploads = _latest(store, user.company_id, period)
    missing = [k for k in ("employees", "timesheet") if k not in uploads]
    if missing:
        raise HTTPException(409, f"Pirma įkelkite failus: {', '.join(missing)}")
    result = _guard(lambda: _run_check(store, user.company_id, period, uploads), "tabelio patikra")
    used = ["employees", "timesheet", *[k for k in OPTIONAL_KINDS if k in uploads]]
    run_id = approval.record_run(store, user.company_id, str(period), "timesheet", RULES_VERSION,
                                 [uploads[k]["id"] for k in used], result, user.username)
    return {"run_id": run_id, "inputs": {k: uploads[k]["id"] for k in used}, **result}


@router.post("/periods/{period}/approve")
def approve(period: Period = Depends(get_period), store: Store = Depends(get_store),
            user: auth.User = Depends(auth.require_role(*APPROVERS))):
    run = store.latest_run(user.company_id, str(period), "timesheet")
    if run is None:
        raise HTTPException(409, MSG_NO_RUN)
    try:
        approval_id = approval.approve(store, user.company_id, str(period), "timesheet", run["id"],
                                       user.username, RULES_VERSION)
    except approval.StaleRunError:
        raise HTTPException(409, MSG_STALE_RUN) from None
    return {"approval_id": approval_id, "run_id": run["id"], "is_approved": True}


# ---------- Rivilės tabelio eksportas ----------

def _approved_run(store: Store, cid: int, period: Period) -> dict:
    """Patvirtintas ir vis dar galiojantis tabelio skaičiavimas, kitaip 409."""
    if not approval.is_approved(store, cid, str(period), "timesheet", RULES_VERSION):
        raise HTTPException(409, MSG_NOT_APPROVED)
    return store.get_run(cid, store.active_approval(cid, str(period), "timesheet")["run_id"])


def _approved_inputs(store: Store, cid: int, period: Period) -> dict[str, dict]:
    run = _approved_run(store, cid, period)
    ups = (store.get_upload(cid, i) for i in run["input_upload_ids"])
    return {u["kind"]: u for u in ups}


def _build_rivile_parts(inputs: dict[str, dict], period: Period, out: Path) -> list[Path]:
    master = {e.tab_no: e.rivile_code for e in parse_employees(inputs["employees"]["stored_path"])}
    rows = timesheet.load_timesheet(inputs["timesheet"]["stored_path"], (period.year, period.month))
    return rivile_payroll.timesheet_to_rivile_xlsx_parts(
        rows, str(period), master, out / f"rivile_tabelis_{period}.xlsx", year=period.year, month=period.month)


def _file_response(parts: list[Path], period: Period) -> Response:
    if len(parts) == 1:
        return _attachment(parts[0].read_bytes(), parts[0].name, XLSX_TYPE)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in parts:
            zf.write(p, p.name)
    return _attachment(buf.getvalue(), f"rivile_tabelis_{period}.zip", "application/zip")


def _attachment(content: bytes, filename: str, media_type: str) -> Response:
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/periods/{period}/rivile-timesheet")
def rivile_timesheet(period: Period = Depends(get_period), store: Store = Depends(get_store),
                     user: auth.User = Depends(auth.current_user)):
    inputs = _approved_inputs(store, user.company_id, period)
    with tempfile.TemporaryDirectory(prefix="uno_rivile_") as tmp:
        parts = _guard(lambda: _build_rivile_parts(inputs, period, Path(tmp)), "Rivilės tabelio eksportas")
        response = _file_response(parts, period)
    store.log(user.company_id, user.username, "export", f"rivile_timesheet {period} ({len(parts)} f.)", str(period))
    return response


# ---------- Rivilės DU palyginimas ----------

def _rekey(rows: list[RivilePayRow], by_code: dict[str, str]) -> list[RivilePayRow]:
    """Rivilės faile gali būti darbuotojo kodas vietoj tabelio nr.: verčiame į tabelio nr.; dublikatai -> 422."""
    rekeyed = [dataclasses.replace(r, key=by_code.get(r.key, r.key)) for r in rows]
    dupes = sorted(k for k, n in Counter(r.key for r in rekeyed).items() if n > 1)
    if dupes:
        raise HTTPException(422, {
            "message": "Rivilės DU faile po kodų pakeitimo tabelio numeriais pasikartoja darbuotojai",
            "duplicates": dupes})
    return rekeyed


def _sam(rows: list[RivilePayRow]) -> D:
    return sum((money(a.sodra_employee) + money(a.sodra_employer) for a in rows), D(0))


def _totals(expected: dict[str, dict], actual: list[RivilePayRow]) -> dict:
    """Patikrinto pogrupio sumos: mūsų skaičiavimas prieš Rivilę."""
    out = {}
    for f in PAY_FIELDS:
        ours = sum((money(e[f]) for e in expected.values()), D(0))
        theirs = sum((money(getattr(a, f)) for a in actual), D(0))
        out[f] = {"ours": str(ours), "rivile": str(theirs), "diff": str(theirs - ours)}
    ours_sam = out["sodra_employee"]["ours"], out["sodra_employer"]["ours"]
    sam = {"ours": str(sum(map(D, ours_sam), D(0))), "rivile": str(_sam(actual))}
    sam["diff"] = str(D(sam["rivile"]) - D(sam["ours"]))
    return {"gpm313": out["gpm"], "sam": sam, "fields": out}


def _file_totals(actual: list[RivilePayRow]) -> dict:
    """VISO Rivilės failo sumos (deklaracijoms), nepriklausomai nuo to, kiek darbuotojų patikrinta."""
    return {"rows": len(actual),
            "gpm313": str(sum((money(a.gpm) for a in actual), D(0))),
            "sam": str(_sam(actual)),
            "gross": str(sum((money(a.gross) for a in actual), D(0))),
            "net": str(sum((money(a.net) for a in actual), D(0)))}


def _coverage(verified: int, total: int) -> dict:
    return {"verified": verified, "total": total, "partial": verified < total,
            "label": f"patikrinta {verified} iš {total} darbuotojų"}


def _compare_run(period: Period, run: dict, payroll_upload_id: int, payroll_path: str,
                 master: dict[str, str]) -> dict:
    expected_all = run["result"]["expected"]
    supported = {t: e for t, e in expected_all.items() if e["supported"]}
    unsupported_keys = {t for t, e in expected_all.items() if not e["supported"]}
    actual = _rekey(rivile_payroll.read_rivile_payroll_xlsx(payroll_path), master)
    compared = [a for a in actual if a.key not in unsupported_keys]
    findings = rivile_payroll.compare(
        {t: {f: D(e[f]) for f in PAY_FIELDS} for t, e in supported.items()}, compared)
    plain = [_plain(f) for f in findings]
    verified_rows = [a for a in compared if a.key in supported]
    coverage = _coverage(len(verified_rows), len({*expected_all, *(a.key for a in actual)}))
    status = "sutampa" if not plain else "neatitikimai"
    note = "; likusieji tikrinami rankiniu būdu" if coverage["partial"] else ""
    return {"period": str(period), "timesheet_run_id": run["id"],
            "input_upload_ids": sorted([*run["input_upload_ids"], payroll_upload_id]),
            "counts": _counts(plain), "findings": plain,
            "status": status, "coverage": coverage,
            "status_label": f"Patikrintas pogrupis: {status} ({coverage['label']}){note}",
            "totals": _totals(supported, verified_rows),
            "file_totals": _file_totals(actual),
            "unverified_totals": _file_totals([a for a in actual if a.key not in supported]),
            "compared": len(supported), "accountant_review": run["result"]["unsupported"]}


@router.post("/periods/{period}/compare")
def compare(period: Period = Depends(get_period), store: Store = Depends(get_store),
            user: auth.User = Depends(auth.require_role(*WRITERS))):
    cid = user.company_id
    uploads = _latest(store, cid, period)
    if "rivile_payroll" not in uploads:
        raise HTTPException(409, "Pirma įkelkite Rivilės DU eksportą (rivile_payroll)")
    run = _approved_run(store, cid, period)
    payroll = uploads["rivile_payroll"]
    master = _codes(store, cid, run)
    result = _guard(lambda: _compare_run(period, run, payroll["id"], payroll["stored_path"], master),
                    "Rivilės DU palyginimas")
    run_id = approval.record_run(store, cid, str(period), "payroll_compare", RULES_VERSION,
                                 result["input_upload_ids"], result, user.username)
    return {"run_id": run_id, **result}


def _codes(store: Store, cid: int, run: dict) -> dict[str, str]:
    """Rivilės kodas -> tabelio nr. iš darbuotojų failo, naudoto tabelio patikroje."""
    for upload_id in run["input_upload_ids"]:
        upload = store.get_upload(cid, upload_id)
        if upload and upload["kind"] == "employees":
            return {e.rivile_code: e.tab_no for e in parse_employees(upload["stored_path"])}
    return {}


# ---------- išsaugoti rezultatai ----------

@router.get("/periods/{period}/runs/{module}")
def saved_run(module: str, period: Period = Depends(get_period), store: Store = Depends(get_store),
              user: auth.User = Depends(auth.require_role(*READERS))):
    """Pilnas paskutinio skaičiavimo rezultatas (kad UI galėtų jį atkurti perkrovus puslapį)."""
    if module not in ("timesheet", "payroll_compare"):
        raise HTTPException(404, f"Nežinomas modulis: {module}")
    run = store.latest_run(user.company_id, str(period), module)
    if run is None:
        raise HTTPException(404, "Šiam laikotarpiui skaičiavimo dar nėra")
    reasons = stale_reasons(store, user.company_id, period, module, run)
    return {"run_id": run["id"], "module": module, "period": run["period"], "created_at": run["created_at"],
            "is_current": not reasons, "stale_reasons": reasons,
            "rules_version": run["rules_version"], "input_upload_ids": run["input_upload_ids"],
            "result": run["result"]}


# ---------- auditas ----------

@router.get("/periods/{period}/audit")
def audit(period: Period = Depends(get_period), store: Store = Depends(get_store),
          user: auth.User = Depends(auth.current_user)):
    return {"entries": store.list_audit(user.company_id, period=str(period), limit=500)}
