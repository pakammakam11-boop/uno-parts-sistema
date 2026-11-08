"""Bendras prisijungimo patikrinimas VPS diegime.

* GET /_gate: Caddy `forward_auth` kviečia prieš kiekvieną /marsrutai* ir /out/* užklausą (Maršrutai
  patys prisijungimo neturi). 204 = leisti, 302 į /app = nėra galiojančios sesijos.
* require_login: dependency demo maršrutams (/apskaita, /api/overview, ...), kai UNO_DEMO_AUTH=1.

Tinka UNO sesija (app.auth, slapukas uno_session: /app prisijungimas) arba personalo sesija
(core.hr.login, slapukas uno_hr_session: el. pašto nuoroda) tik direktoriui / hr (apskaitos demo, kaip
/api/dash/access); /_gate dar praleidžia vadovus (turinčius pavaldinių). Paprastas darbuotojas: 403. Ne-demo maršrutai savo apsaugą turi patys.
"""
from __future__ import annotations

import hmac
import os
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app import auth, hr_api
from core.dashboard import connectors
from core.hr import login as hr_login
from core.hr.errors import HrError

router = APIRouter()
UNO_COMPANY_ID = 1                       # RESULTS, tiekėjai ir eksportai priklauso tik UNO įmonei
ACCOUNTING_WRITE_ROLES = ("accountant", "admin")
ACCOUNTING_READ_ROLES = ("accountant", "admin", "manager")   # vadovas: tik skaitymas
MSG_NEED_LOGIN = "Reikia prisijungti"
MSG_FORBIDDEN = "Neturite teisės šiai zonai"


def demo_auth_required() -> bool:
    return os.environ.get("UNO_DEMO_AUTH", "0") == "1"


def uno_session_ok(request: Request) -> bool:
    """UNO sesija, tinkanti Maršrutams / apskaitai: UNO įmonė ir apskaitos rolė (ne kitos įmonės naudotojas)."""
    user = uno_user(request)
    return user is not None and user.company_id == UNO_COMPANY_ID and user.role in ACCOUNTING_READ_ROLES


def hr_actor(request: Request):
    """Galiojančios personalo sesijos darbuotojas arba None."""
    if not request.cookies.get(hr_login.COOKIE_NAME):
        return None
    try:
        with hr_api.open_tx(request) as conn:
            return hr_api.authed(conn, request)
    except HrError:
        return None


def hr_session_ok(request: Request, *, allow_managers: bool = False) -> bool:
    """Personalo sesija su prieiga prie savininko zonos: direktorius arba hr; Maršrutams dar vadovai
    (turintys pavaldinių). Paprastas darbuotojas negauna nieko."""
    actor = hr_actor(request)
    if actor is None:
        return False
    return actor.id == connectors.DIRECTOR_ID or actor.is_hr or (allow_managers and actor.is_manager)


def session_ok(request: Request, *, allow_managers: bool = False) -> bool:
    return uno_session_ok(request) or hr_session_ok(request, allow_managers=allow_managers)


def uno_user(request: Request) -> auth.User | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    if not token:
        return None
    with closing(auth.connect()) as conn:
        return auth.load_user(conn, token)


def _csrf_ok(sent: str, expected: str) -> bool:
    return hmac.compare_digest(sent.encode(), expected.encode())


def require_login(request: Request) -> None:
    """Apskaitos (demo) maršrutai. UNO_DEMO_AUTH!=1 (vietinis demo be prisijungimo): atviri. VPS / gamyboje
    UNO_DEMO_AUTH=1 PRIVALOMA:
      * UNO sesija: tik UNO įmonė (company_id=1); accountant/admin skaito ir rašo, manager tik skaito;
        rašymui (POST/PUT/DELETE/PATCH) reikia galiojančio CSRF (X-CSRF-Token).
      * personalo sesija: tik direktorius / hr (kaip /api/dash/access); rašymui irgi CSRF.
    Kitaip 401 (nėra sesijos) arba 403."""
    if not demo_auth_required():
        return
    unsafe = request.method in auth.UNSAFE_METHODS
    sent = request.headers.get(auth.CSRF_HEADER, "")
    user = uno_user(request)
    if user is not None:
        allowed = ACCOUNTING_WRITE_ROLES if unsafe else ACCOUNTING_READ_ROLES
        if user.company_id != UNO_COMPANY_ID or user.role not in allowed:
            raise HTTPException(403, MSG_FORBIDDEN)
        if unsafe and not _csrf_ok(sent, user.csrf):
            raise HTTPException(403, auth.MSG_CSRF)
        return
    actor = hr_actor(request)
    if actor is None:
        raise HTTPException(401, MSG_NEED_LOGIN)
    if not hr_session_ok(request):
        raise HTTPException(403, MSG_FORBIDDEN)
    if unsafe and not _csrf_ok(sent, actor.csrf):
        raise HTTPException(403, hr_login.MSG_CSRF)


@router.get("/_gate", include_in_schema=False)
def gate(request: Request):
    # Maršrutai ir /out/*: direktorius, hr ir vadovai; UNO (buhalterių) sesija taip pat tinka.
    if session_ok(request, allow_managers=True):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    if hr_actor(request) is not None:
        return Response(status_code=403, headers={"Cache-Control": "no-store"})
    return RedirectResponse("/app", status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/healthz", include_in_schema=False)
def healthz():
    """Konteinerio / Caddy sveikatos patikra: be duomenų, be prisijungimo."""
    return {"status": "ok"}
