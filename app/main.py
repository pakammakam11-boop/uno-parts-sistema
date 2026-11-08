"""UNO Parts apskaitos demo serveris. Veikia tik localhost, duomenys niekur neišeina.

Paleidimas iš projekto šaknies:  py -3.12 -m uvicorn app.main:app --port 8770
"""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import api_v1, auth, dashboard_api, export_api, gama_api, gate, hr_api, services
from core import ai_bridge, control, mail_import, suppliers

STATIC = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifestas (Add to Home Screen)
RUNNERS = {"invoices": services.run_invoices, "bank": services.run_bank, "payroll": services.run_payroll}
UPLOAD_EXT = {"invoices": ".pdf", "bank": ".xml", "payroll": ".xlsx"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

log = logging.getLogger("uno.main")

_TOKEN_PATH = re.compile(r"(/hr/(?:login|sign)/)[^/\s?\"']+")


class MaskTokens(logging.Filter):
    """Paslepia /hr/login/<žetonas> ir /hr/sign/<žetonas> bet kuriame žurnalo įraše (uvicorn.access ir pan.)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN_PATH.sub(r"\1***", str(record.getMessage()))
        record.args = ()
        return True


for _name in ("uvicorn.access", "uvicorn.error", "uno"):
    logging.getLogger(_name).addFilter(MaskTokens())


def demo_enabled() -> bool:
    """UNO_DEMO=0 (gamyba) išjungia demo maršrutus; numatytai jie įjungti (demo pristatymui)."""
    return os.environ.get("UNO_DEMO", "1") != "0"


def require_demo(request: Request) -> None:
    if not demo_enabled():
        raise HTTPException(404, "Nerasta")
    gate.require_login(request)  # UNO_DEMO_AUTH=1 (VPS): demo maršrutai tik prisijungusiems


app = FastAPI(title="UNO Parts apskaita (demo)")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """/api/login per ilgi laukai -> 400 lietuviškai (be įvesties aido); kitur standartinis 422."""
    if request.url.path == "/api/login":
        return JSONResponse({"detail": auth.MSG_LOGIN_FIELDS}, status_code=400)
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Naršyklė visada patikrina, ar JS/CSS nepasikeitė (ETag), kad po atnaujinimo nebūtų senos versijos."""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in ("/", "/app", "/apskaita"):
        response.headers["Cache-Control"] = "no-cache"
    return response


demo = APIRouter(dependencies=[Depends(require_demo)])
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(auth.router)
app.include_router(gate.router)
app.include_router(api_v1.router)
app.include_router(gama_api.router)
app.include_router(hr_api.router)
app.include_router(hr_api.pages)
app.include_router(dashboard_api.router)
app.include_router(export_api.router)
RESULTS: dict[str, dict] = {}  # paskutiniai modulių rezultatai (vieno naudotojo demo)


def public(result: dict) -> dict:
    """Be vidinių laukų (prasideda _), kad JSON būtų švarus."""
    return {k: v for k, v in result.items() if not k.startswith("_")}


@app.get("/app")
def production_app():
    """Gamybinis DU kontrolės ekranas (su prisijungimu). Apskaitos demo: /apskaita."""
    return FileResponse(STATIC / "prod.html")


@app.get("/hr")
def hr_app():
    """Personalo prašymų modulis (atostogos, komandiruotės, demo e. parašas)."""
    return hr_api.hr_page()


@app.get("/")
def dashboard():
    """Savininko apžvalga (dashboard). Duomenis saugo HR sesija, žr. app/dashboard_api.py."""
    return dashboard_api.page()


@demo.get("/apskaita")
def index():
    """Apskaitos demo (buvęs /)."""
    return FileResponse(STATIC / "index.html")


@demo.get("/api/overview")
def overview():
    ledger = []
    for module, line, price, what, auto, pct in control.PRICE_LIST:
        res = RESULTS.get(module)
        result = res["summary"] if res and module != "control" else ""
        if module == "control" and RESULTS:
            result = "Sumos ir laiškai paruošti, žr. Kontrolė"
        ledger.append({"module": module, "line": line, "price": f"{price:,}".replace(",", " "),
                       "what": what, "auto": auto, "auto_pct": pct, "result": result})
    total = sum(p[2] for p in control.PRICE_LIST)
    weighted = sum(p[2] * p[5] for p in control.PRICE_LIST) / total
    return {
        "period": "2026 m. spalis",
        "ledger": ledger,
        "total_price": f"{total:,}".replace(",", " "),
        "total_note": f"Svertinis vidurkis: apie {weighted:.0f} % darbo atlieka sistema.",
        "checklist": control.checklist(RESULTS),
        "badges": _badges(),
    }


def _badges() -> dict:
    out = {k: r["counts"] for k, r in RESULTS.items()}
    if RESULTS:
        found = control.all_findings(RESULTS)
        out["control"] = services.counts(found)
        out["declarations"] = control.declarations(RESULTS)["counts"]
    return out


@demo.post("/api/run/{module}")
def run(module: str):
    if module not in RUNNERS:
        raise HTTPException(404, "Toks modulis neegzistuoja")
    RESULTS[module] = RUNNERS[module]()
    return {**public(RESULTS[module]), "badges": _badges()}


@demo.get("/api/module/{module}")
def last(module: str):
    if module not in RUNNERS:
        raise HTTPException(404, "Toks modulis neegzistuoja")
    if module == "invoices":
        services.sync_mail_invoices(RESULTS)  # VPS: pašto procesas (worker) importuoja atskirai
    res = RESULTS.get(module)
    return {**public(res), "badges": _badges()} if res else {"empty": True}


@demo.post("/api/upload/{module}")
async def upload(module: str, files: list[UploadFile] = File(...)):
    if module not in RUNNERS:
        raise HTTPException(404, "Toks modulis neegzistuoja")
    ext = UPLOAD_EXT[module]
    tmp = Path(tempfile.mkdtemp(prefix=f"uno_{module}_"))
    try:
        saved = []
        for f in files:
            name = Path(f.filename or "").name
            if not name.lower().endswith(ext):
                raise HTTPException(400, f"Tinka tik {ext} failai: {name}")
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(400, f"Failas per didelis: {name}")
            (tmp / name).write_bytes(data)
            saved.append(tmp / name)
        arg = tmp if module == "invoices" else saved[0]
        try:
            RESULTS[module] = RUNNERS[module](arg)
        except HTTPException:
            raise
        except Exception as e:  # įkėlimo riba: bet koks sugadinto failo formatas -> suprantamas 422, ne 500
            log.exception("Demo failo apdorojimas nepavyko")
            raise HTTPException(422, "Nepavyko apdoroti failo: patikrinkite failo formatą") from e
        return {**public(RESULTS[module]), "badges": _badges()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@demo.get("/api/download/{name:path}")
def download(name: str):
    path = (services.OUT / name).resolve()
    if services.OUT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Failas nerastas")
    return FileResponse(path, filename=path.name)


@demo.post("/api/payroll/calc")
def payroll_calc(net: str = Form(...), fixed_term: str = Form("0")):
    try:
        value = Decimal(net.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as e:
        raise HTTPException(400, "Įveskite sumą skaičiais") from e
    if value < 0 or value > 50000:
        raise HTTPException(400, "Suma turi būti tarp 0 ir 50 000 €")
    return services.calc(value, fixed_term == "1")


@demo.get("/api/control")
def control_view():
    _ensure_results()
    found = control.all_findings(RESULTS)
    drafts = control.drafts(RESULTS)
    c = services.counts(found)
    return {
        "kpis": [
            {"label": "Klaidos", "value": str(c["error"])},
            {"label": "Įspėjimai", "value": str(c["warning"])},
            {"label": "Laiškų juodraščiai", "value": str(len(drafts))},
        ],
        "findings": [services.finding_dict(f) for f in found],
        "drafts": [d.__dict__ for d in drafts],
        "badges": _badges(),
    }


def _ensure_results() -> None:
    if not RESULTS:
        for key, runner in RUNNERS.items():
            RESULTS[key] = runner()


@demo.get("/api/declarations")
def declarations_view():
    _ensure_results()
    d = control.declarations(RESULTS)
    return {**d, "findings": [services.finding_dict(f) for f in d["findings"]], "badges": _badges()}


@demo.get("/api/rivile-sync")
def rivile_sync():
    """Kada Rivilės serveris (VM suplanuota užduotis) paskutinį kartą kreipėsi į EIP eksportą."""
    return export_api.last_ping()


@demo.post("/api/invoices/mail")
def invoices_from_mail():
    """Pirkimo sąskaitos iš pašto dėžutės (IMAP, tik skaitymas): PDF priedai -> tas pats patikros srautas."""
    try:
        RESULTS["invoices"] = services.run_invoices_mail()
    except mail_import.MailNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    except mail_import.MailError as e:
        raise HTTPException(502, str(e)) from e
    return {**public(RESULTS["invoices"]), "badges": _badges()}


class NewSupplier(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company_code: str = Field("", max_length=20)
    vat_code: str = Field("", max_length=20)
    iban: str = Field("", max_length=40)


@demo.post("/api/suppliers")
def create_supplier(body: NewSupplier):
    """Naujas tiekėjas iš radinio „Nežinomas tiekėjas“; sąskaitos perskaičiuojamos be pašto/AI kvietimų."""
    try:
        sup, created = suppliers.add(body.name, body.company_code, body.vat_code, body.iban)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if RESULTS.get("invoices", {}).get("_source"):
        RESULTS["invoices"] = services.rerun_invoices(RESULTS["invoices"])
    out = public(RESULTS["invoices"]) if "invoices" in RESULTS else {}
    return {**out, "supplier": {"code": sup.rivile_code, "name": sup.name, "created": created}, "badges": _badges()}


@demo.post("/api/ai/package")
def ai_package():
    if not RESULTS:
        raise HTTPException(400, "Pirma paleiskite uždarymą")
    found = [f for f in control.all_findings(RESULTS) if f.severity in ("error", "warning")]
    names = RESULTS.get("payroll", {}).get("_names", [])            # vardai iš įkelto tabelio
    remits = [r["text"] for r in RESULTS.get("bank", {}).get("table", {}).get("rows", [])]
    parties = [r["party"] for r in RESULTS.get("bank", {}).get("table", {}).get("rows", [])]
    parties += [r["supplier"] for r in RESULTS.get("invoices", {}).get("table", {}).get("rows", [])]
    return ai_bridge.build_package(found, extra_people=names, free_texts=remits, structured_names=parties)


@demo.post("/api/ai/restore")
def ai_restore(session: str = Form(...), text: str = Form(...)):
    try:
        return {"text": ai_bridge.restore(session, text)}
    except KeyError as e:
        raise HTTPException(404, "Sesija nerasta") from e


app.include_router(demo)
