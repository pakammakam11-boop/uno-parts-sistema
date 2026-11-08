"""Personalo prašymų API (/api/hr/*) ir puslapių maršrutai (/hr/login/{token}, /hr/sign/...). Žr. SPEC.md.

Kiekvienas maršrutas: viena DB transakcija (core.hr.db.tx); klaidos {"detail": "..."} (HrError -> JSON).
"""
from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from core.hr import db, flow, login, people, seed, service, views, workdays
from core.hr.errors import HrError, forbidden, not_found

HR_HTML = Path(__file__).parent / "static" / "hr.html"
NO_CACHE = {"Cache-Control": "no-cache"}
MSG_BAD_INPUT = "Neteisingi duomenys: patikrinkite užklausą."
PLACEHOLDER = "<!doctype html><meta charset=utf-8><title>Personalo prašymai</title><p>Personalo modulio sąsaja dar nepaskelbta.</p>"


class HrRoute(APIRoute):
    """HrError -> {"detail": "..."} su atitinkamu HTTP kodu; FastAPI 422 -> 400 lietuviškai;
    kiekvienas sėkmingas autentifikuotas atsakymas atnaujina sesijos slapuko galiojimą."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except HrError as e:
                return JSONResponse({"detail": e.detail}, status_code=e.status)
            except RequestValidationError:
                return JSONResponse({"detail": MSG_BAD_INPUT}, status_code=400)
            token = getattr(request.state, "hr_session_token", None)
            if token and response.status_code < 400 and "set-cookie" not in response.headers:
                set_session_cookie(response, token)
            return response
        return handler


router = APIRouter(prefix="/api/hr", route_class=HrRoute)
pages = APIRouter(route_class=HrRoute)


def demo_enabled() -> bool:
    return os.environ.get("HR_DEMO") == "1"


def require_demo() -> None:
    if not demo_enabled():
        raise HTTPException(404, "Nerasta")


def base_url(request: Request) -> str:
    return (os.environ.get("HR_BASE_URL") or str(request.base_url)).rstrip("/")


def open_tx(request: Request):
    seed.ready()
    return db.tx(base_url(request))


def authed(conn, request: Request, *, write: bool = False) -> people.Actor:
    token = request.cookies.get(login.COOKIE_NAME)
    actor = login.load_actor(conn, token) if token else None
    if actor is None:
        raise HrError(401, login.MSG_NEED_LOGIN)
    request.state.hr_session_token = token
    if write:
        login.check_csrf(actor, request.headers.get(login.CSRF_HEADER, ""))
    return actor


def secure_cookie() -> bool:
    """Secure slapukas: Vercel (VERCEL=1) arba aiškiai HR_SECURE_COOKIE=1."""
    return os.environ.get("VERCEL") == "1" or os.environ.get("HR_SECURE_COOKIE") == "1"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(login.COOKIE_NAME, token, max_age=login.SESSION_IDLE_SECONDS, httponly=True,
                        samesite="lax", secure=secure_cookie(), path="/")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------- prisijungimas ----------

@router.get("/me")
def me(request: Request):
    with open_tx(request) as conn:
        actor = authed(conn, request)
        return {**views.me(conn, actor), "demo": demo_enabled()}


@router.get("/demo/personas")
def personas(request: Request):
    require_demo()
    with open_tx(request) as conn:
        out = []
        for emp in people.employees_map(conn).values():
            actor = people.make_actor(conn, emp, "")
            out.append({"id": emp["id"], "name": emp["name"], "position": emp["position"],
                        "department": emp["department"], "roles": actor.roles})
        return out


@router.post("/demo/login")
def demo_login(request: Request, response: Response, body: dict = Body(...)):
    require_demo()
    with open_tx(request) as conn:
        emp_id = body.get("employee_id")
        employee = people.get_employee(conn, emp_id) if isinstance(emp_id, int) else None
        if employee is None:
            raise not_found("Darbuotojas nerastas")
        token, csrf = login.create_session(conn, employee["id"])
        actor = people.make_actor(conn, employee, csrf)
        set_session_cookie(response, token)
        return {**views.me(conn, actor), "demo": True}


@router.post("/login/request")
def login_request(request: Request, body: dict = Body(...)):
    """Visada {ok:true}: nenurodome, ar toks el. paštas egzistuoja."""
    with open_tx(request) as conn:
        login.request_magic_link(conn, str(body.get("email") or ""), client_ip(request))
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    with open_tx(request) as conn:
        token = request.cookies.get(login.COOKIE_NAME)
        if token and (actor := login.load_actor(conn, token)):
            login.check_csrf(actor, request.headers.get(login.CSRF_HEADER, ""))
            login.delete_session(conn, token)
    response.delete_cookie(login.COOKIE_NAME, path="/")
    return {"ok": True}


@pages.get("/hr/login/{token}")
def magic_link(token: str, request: Request):
    with open_tx(request) as conn:
        employee_id = login.consume_magic_link(conn, token)
        if employee_id is None:
            return RedirectResponse("/hr?link=invalid", status_code=303)
        session_token, _ = login.create_session(conn, employee_id)
    response = RedirectResponse("/hr", status_code=303)
    set_session_cookie(response, session_token)
    return response


def hr_page() -> Response:
    if HR_HTML.exists():
        return FileResponse(HR_HTML, headers=NO_CACHE)
    return HTMLResponse(PLACEHOLDER, headers=NO_CACHE)


@pages.get("/hr/sign/{token}/{signer_id}")
def sign_page(token: str, signer_id: str):
    """Demo „Dokobit“ pasirašymo puslapis: ta pati SPA, maršrutą skaito JS."""
    return hr_page()


# ---------- pagalbiniai ----------

@router.get("/colleagues")
def colleagues(request: Request):
    with open_tx(request) as conn:
        actor = authed(conn, request)
        emps = people.employees_map(conn)
        return [{"id": e["id"], "name": e["name"], "position": e["position"], "department": e["department"]}
                for e in sorted(emps.values(), key=lambda e: e["name"])
                if e["id"] not in (actor.id, actor.employee["manager_id"])]  # vadovas negali būti pavaduojantis


@router.post("/workdays")
def workdays_count(request: Request, body: dict = Body(...)):
    with open_tx(request) as conn:
        authed(conn, request, write=True)
    start, end = service.parse_range(body.get("date_from"), body.get("date_to"))
    return {"work_days": workdays.work_days(start, end), "holidays": workdays.holidays_between(start, end)}


# ---------- prašymai ----------

@router.get("/requests")
def list_requests(request: Request, scope: str = "mine"):
    with open_tx(request) as conn:
        return views.list_requests(conn, authed(conn, request), scope)


@router.post("/requests")
def create_request(request: Request, body: dict = Body(...)):
    with open_tx(request) as conn:
        actor = authed(conn, request, write=True)
        request_id, warnings = service.create_request(conn, actor, body)
        return {**views.request_detail(conn, actor, request_id), "warnings": warnings}


@router.get("/requests/{request_id}")
def request_detail(request_id: int, request: Request):
    with open_tx(request) as conn:
        return views.request_detail(conn, authed(conn, request), request_id)


@router.post("/requests/{request_id}/cancel")
def cancel_request(request_id: int, request: Request):
    with open_tx(request) as conn:
        actor = authed(conn, request, write=True)
        service.cancel_request(conn, actor, request_id)
        return views.request_detail(conn, actor, request_id)


# ---------- pasirašymas ----------

@router.get("/sign/{token}/{signer_id}")
def sign_info(token: str, signer_id: str, request: Request):
    with open_tx(request) as conn:
        return views.sign_info(conn, authed(conn, request), token, signer_id)


@router.post("/sign/{token}/{signer_id}")
def sign(token: str, signer_id: str, request: Request, body: dict = Body(...)):
    with open_tx(request) as conn:
        actor = authed(conn, request, write=True)
        request_id = flow.sign(conn, actor, token, signer_id, str(body.get("action") or ""), body.get("reason"))
        return views.request_detail(conn, actor, request_id)


@router.post("/postback")
def postback(request: Request, body: dict = Body(...)):
    """Dokobit webhook. Demo viduje kviečia flow.handle_postback tiesiogiai; per HTTP tik su bendra paslaptimi."""
    secret = os.environ.get("HR_POSTBACK_SECRET", "")
    sent = request.headers.get("X-Postback-Secret", "")
    if not secret or not hmac.compare_digest(sent.encode(), secret.encode()):
        raise forbidden("Postback atmestas: neteisingas arba nenustatytas parašas.")
    with open_tx(request) as conn:
        return flow.handle_postback(conn, token=str(body.get("token") or ""), signer_id=body.get("signer_id"),
                                    event=str(body.get("event") or ""), reason=body.get("reason"))


@router.get("/documents/{doc_id:int}.pdf")
def document(doc_id: int, request: Request, signed: int = 0):
    with open_tx(request) as conn:
        content, filename = views.fetch_pdf(conn, authed(conn, request), doc_id, bool(signed))
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- tabelis, laiškai, demo ----------

@router.get("/timesheet")
def timesheet(request: Request, month: str | None = None):
    with open_tx(request) as conn:
        return views.timesheet(conn, authed(conn, request), month)


@router.get("/outbox")
def outbox(request: Request):
    with open_tx(request) as conn:
        return views.outbox(conn, authed(conn, request))


@router.post("/demo/reset")
def demo_reset(request: Request):
    require_demo()
    with open_tx(request) as conn:
        actor = authed(conn, request, write=True)
        if not actor.is_hr:
            raise forbidden("Demo duomenis gali atstatyti tik personalo (HR) darbuotojas.")
        seed.reset(conn)
    return {"ok": True}
