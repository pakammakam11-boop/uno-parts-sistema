"""Savininko dashboard API (/api/dash/*). Prieiga pagal HR sesiją: direktorius (id 1) arba buhalterė (hr rolė).

Klaidos {"detail": "..."}: 400 (parametrai), 401 (nėra sesijos), 403 (ne direktorius / hr), 404 (nėra grupės / tiekėjo).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse

from app import hr_api
from core.dashboard import connectors, costs, efficiency, metrics, operations, store
from core.hr import db
from core.hr.errors import forbidden

DASHBOARD_HTML = Path(__file__).parent / "static" / "dashboard.html"
NO_CACHE = {"Cache-Control": "no-cache"}
PLACEHOLDER = "<!doctype html><meta charset=utf-8><title>Apžvalga</title><p>Apžvalgos sąsaja dar nepaskelbta.</p>"
MSG_FORBIDDEN = "Apžvalgą mato tik direktorius ir buhalterė."

router = APIRouter(prefix="/api/dash", route_class=hr_api.HrRoute)


def page():
    """Pradžios ekranas /: dashboard.html (arba laikinas puslapis, kol sąsajos nėra)."""
    if DASHBOARD_HTML.exists():
        return FileResponse(DASHBOARD_HTML, headers=NO_CACHE)
    return HTMLResponse(PLACEHOLDER, headers=NO_CACHE)


def require_access(request: Request) -> None:
    """HR sesija: 401 be sesijos, 403 ne direktoriui ir ne personalo (hr) darbuotojui."""
    with hr_api.open_tx(request) as conn:
        actor = hr_api.authed(conn, request)
    if not (actor.id == connectors.DIRECTOR_ID or actor.is_hr):
        raise forbidden(MSG_FORBIDDEN)


@router.get("/access")
def access(request: Request):
    """Ar prisijungęs naudotojas mato apžvalgą (UI sprendimui; 401, jei nėra sesijos)."""
    with hr_api.open_tx(request) as conn:
        actor = hr_api.authed(conn, request)
        envelope = connectors.envelope(conn, ["personalas"])
    return {"allowed": actor.id == connectors.DIRECTOR_ID or actor.is_hr, **envelope}


def data_tx(request: Request):
    """Transakcija faktų lentelėms (pirmiau pasėjama / papildoma iki vakar)."""
    store.ready()
    return db.tx(hr_api.base_url(request))


@router.get("/summary", dependencies=[Depends(require_access)])
def summary(request: Request, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return metrics.summary(conn, period, branch)


@router.get("/costs", dependencies=[Depends(require_access)])
def cost_groups(request: Request, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return costs.groups(conn, period, branch)


@router.get("/costs/{group}", dependencies=[Depends(require_access)])
def cost_suppliers(request: Request, group: str, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return costs.suppliers(conn, group, period, branch)


@router.get("/costs/{group}/{supplier}", dependencies=[Depends(require_access)])
def cost_documents(request: Request, group: str, supplier: str, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return costs.documents(conn, group, supplier, period, branch)


@router.get("/sales/series", dependencies=[Depends(require_access)])
def sales_series(request: Request, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return metrics.sales_series(conn, period, branch)


@router.get("/efficiency", dependencies=[Depends(require_access)])
def efficiency_view(request: Request, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return efficiency.efficiency(conn, period, branch)


@router.get("/work", dependencies=[Depends(require_access)])
def work(request: Request):
    with data_tx(request) as conn:
        return operations.work(conn)


@router.get("/operations", dependencies=[Depends(require_access)])
def operations_view(request: Request, period: str = "month", branch: str = "all"):
    with data_tx(request) as conn:
        return operations.operations(conn, period, branch)


@router.get("/sources", dependencies=[Depends(require_access)])
def sources(request: Request):
    with data_tx(request) as conn:
        return {"sources": connectors.describe_sources(conn), **connectors.envelope(conn, [s[0] for s in connectors.SOURCES])}
