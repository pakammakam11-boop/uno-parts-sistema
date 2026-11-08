"""Skaitymo pusė: prašymų sąrašai ir detalės, pasirašymo puslapio info, tabelis, pašto dėžutė, PDF."""
from __future__ import annotations

import calendar
import re
from datetime import date

from sqlalchemy import select

from core.hr import clock, documents, people, service, signing, workdays
from core.hr.errors import bad_request, forbidden, not_found
from core.hr.pdf import LEAVE_LABELS, ROLE_LABELS
from core.hr.schema import hr_audit, hr_documents, hr_marks, hr_outbox, hr_requests, hr_signers

STATUS_LABELS = {"pending_signatures": "Laukia parašų", "approved": "Patvirtintas", "rejected": "Atmestas",
                 "cancelled": "Atšauktas"}
ORDER_LABELS = {"pending_order": "Įsakymas laukia parašo", "order_signed": "Įsakymas pasirašytas",
                "order_rejected": "Įsakymas atmestas"}
SCOPES = ("mine", "to_sign", "team", "all")
OUTBOX_LIMIT = 200


def _short(emp: dict | None) -> dict | None:
    return None if emp is None else {"id": emp["id"], "name": emp["name"], "position": emp["position"],
                                     "department": emp["department"]}


def _signer_view(s: dict, doc: dict, signers: list[dict], emps: dict, actor: people.Actor) -> dict:
    turn = doc["status"] == "pending" and s["status"] == "pending" and all(
        o["status"] == "signed" for o in signers if o["sequence"] < s["sequence"])
    mine = s["employee_id"] == actor.id and doc["status"] == "pending" and s["status"] == "pending"
    return {"signer_id": s["signer_id"], "employee": _short(emps[s["employee_id"]]), "role": s["role"],
            "role_label": ROLE_LABELS[s["role"]], "sequence": s["sequence"], "status": s["status"],
            "signed_at": clock.iso(s["signed_at"]), "method": s["method"], "reason": s["reason"],
            "your_turn": mine and turn,
            "sign_url": signing.get_provider().signer_url(doc["signing_token"], s["signer_id"]) if mine else None}


def _document_view(doc: dict, signers: list[dict], emps: dict, actor: people.Actor) -> dict:
    done = doc["status"] == "completed"
    return {"id": doc["id"], "kind": doc["kind"], "title": doc["title"], "status": doc["status"],
            "sha256": doc["sha256"], "created_at": clock.iso(doc["created_at"]),
            "pdf_url": f"/api/hr/documents/{doc['id']}.pdf",
            "signed_url": f"/api/hr/documents/{doc['id']}.pdf?signed=1" if done else None,
            "signers": [_signer_view(s, doc, signers, emps, actor) for s in signers]}


def _load(conn, request_ids: list[int]) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    docs = [dict(r) for r in conn.execute(select(hr_documents).where(hr_documents.c.request_id.in_(request_ids))
                                          .order_by(hr_documents.c.id)).mappings()]
    sig_rows = conn.execute(select(hr_signers).where(hr_signers.c.document_id.in_([d["id"] for d in docs]))
                            .order_by(hr_signers.c.sequence)).mappings().all() if docs else []
    signers: dict[int, list[dict]] = {}
    for r in sig_rows:
        signers.setdefault(r["document_id"], []).append(dict(r))
    by_request: dict[int, list[dict]] = {}
    for d in docs:
        by_request.setdefault(d["request_id"], []).append(d)
    return by_request, signers


def serialize_many(conn, actor: people.Actor, reqs: list[dict]) -> list[dict]:
    if not reqs:
        return []
    emps = people.employees_map(conn)
    docs_by_req, signers = _load(conn, [r["id"] for r in reqs])
    out = []
    for r in reqs:
        docs = [_document_view(d, signers.get(d["id"], []), emps, actor) for d in docs_by_req.get(r["id"], [])]
        out.append(_request_view(r, docs, emps, actor))
    return out


def _request_view(r: dict, docs: list[dict], emps: dict, actor: people.Actor) -> dict:
    mine = [s for d in docs for s in d["signers"] if s["sign_url"]]
    turn = next((s for s in mine if s["your_turn"]), None)
    owner = emps[r["employee_id"]]
    manager_signed = any(s["role"] == "manager" and s["status"] == "signed"
                         for d in docs if d["kind"] == "request" for s in d["signers"])
    return {
        "id": r["id"], "number": documents.request_number(r["id"], r["created_at"]), "type": r["type"],
        "leave_kind": r["leave_kind"], "leave_kind_label": LEAVE_LABELS.get(r["leave_kind"] or ""),
        "employee": _short(owner), "substitute": _short(emps.get(r["substitute_id"])),
        "manager": _short(emps.get(owner["manager_id"])),
        "date_from": r["date_from"].isoformat(), "date_to": r["date_to"].isoformat(), "work_days": r["work_days"],
        "destination": r["destination"], "purpose": r["purpose"], "comment": r["comment"],
        "status": r["status"], "status_label": STATUS_LABELS[r["status"]], "reason": r["reason"],
        "declined_by": _short(emps.get(r["declined_by"])) if r["declined_by"] else None,
        "order_status": r["order_status"], "order_status_label": ORDER_LABELS.get(r["order_status"] or ""),
        "created_at": clock.iso(r["created_at"]), "documents": docs,
        "signers": next((d["signers"] for d in docs if d["kind"] == "request"), []),
        "sign_url": turn["sign_url"] if turn else None, "my_turn": turn is not None,
        "my_sign_url": mine[0]["sign_url"] if mine else None,   # mano nepasirašytas parašas, net jei dar ne mano eilė
        "can_cancel": r["employee_id"] == actor.id and r["status"] == "pending_signatures" and not manager_signed,
    }


def _scope_filter(conn, actor: people.Actor, scope: str):
    if scope == "mine":
        return hr_requests.c.employee_id == actor.id
    if scope == "team":
        if not actor.is_manager:
            raise forbidden("Komandos prašymus mato tik vadovai.")
        return hr_requests.c.employee_id.in_(people.subordinate_ids(conn, actor.id))
    if scope == "all":
        if not actor.is_hr:
            raise forbidden("Visus prašymus mato tik personalo (HR) darbuotojai.")
        return hr_requests.c.id > 0
    raise bad_request("Neteisingas scope (mine, to_sign, team, all).")


def list_requests(conn, actor: people.Actor, scope: str) -> list[dict]:
    if scope == "to_sign":
        return _to_sign(conn, actor)
    cond = _scope_filter(conn, actor, scope)
    rows = conn.execute(select(hr_requests).where(cond).order_by(hr_requests.c.id.desc())).mappings().all()
    return serialize_many(conn, actor, [dict(r) for r in rows])


def _to_sign(conn, actor: people.Actor) -> list[dict]:
    rows = conn.execute(select(hr_requests).distinct().join(hr_documents, hr_documents.c.request_id == hr_requests.c.id)
                        .join(hr_signers, hr_signers.c.document_id == hr_documents.c.id)
                        .where(hr_signers.c.employee_id == actor.id, hr_signers.c.status == "pending",
                               hr_documents.c.status == "pending")
                        .order_by(hr_requests.c.id.desc())).mappings().all()
    return [v for v in serialize_many(conn, actor, [dict(r) for r in rows]) if v["my_turn"]]


def get_visible_request(conn, actor: people.Actor, request_id: int) -> dict:
    row = conn.execute(select(hr_requests).where(hr_requests.c.id == request_id)).mappings().first()
    if row is None or not people.can_view(conn, actor, dict(row)):
        raise not_found("Prašymas nerastas")
    return dict(row)


def request_detail(conn, actor: people.Actor, request_id: int) -> dict:
    req = get_visible_request(conn, actor, request_id)
    view = serialize_many(conn, actor, [req])[0]
    emps = people.employees_map(conn)
    rows = conn.execute(select(hr_audit).where(hr_audit.c.request_id == request_id)
                        .order_by(hr_audit.c.ts, hr_audit.c.id)).mappings().all()
    view["audit"] = [{"ts": clock.iso(a["ts"]), "actor": _short(emps.get(a["actor_id"])), "action": a["action"],
                      "detail": a["detail"]} for a in rows]
    return view


def sign_info(conn, actor: people.Actor, token: str, signer_id: str) -> dict:
    """Demo „Dokobit“ puslapio duomenys."""
    doc = conn.execute(select(hr_documents).where(hr_documents.c.signing_token == token)).mappings().first()
    signer = None
    if doc is not None:
        signer = conn.execute(select(hr_signers).where(hr_signers.c.document_id == doc["id"],
                                                       hr_signers.c.signer_id == signer_id)).mappings().first()
    if doc is None or signer is None:
        raise not_found("Pasirašymas nerastas")
    req = get_visible_request(conn, actor, doc["request_id"])
    view = serialize_many(conn, actor, [req])[0]
    doc_view = next(d for d in view["documents"] if d["id"] == doc["id"])
    me = next(s for s in doc_view["signers"] if s["signer_id"] == signer_id)
    can_sign = me["your_turn"]
    return {"document": {k: doc_view[k] for k in ("id", "kind", "title", "status", "sha256", "pdf_url")}
            | {"request_id": req["id"]},
            "request": view, "signers": doc_view["signers"], "signer": me, "your_turn": can_sign,
            "can_sign": can_sign, "reason": None if can_sign else _why_not(me, doc_view, actor),
            "control_code": signing.control_code(token, signer_id), "method": signing.DEMO_METHOD}


def _why_not(me: dict, doc_view: dict, actor: people.Actor) -> str:
    if doc_view["status"] != "pending":
        return "Dokumentas nebegalioja (jau užbaigtas, atmestas ar atšauktas)."
    if me["status"] != "pending":
        return "Šis parašas jau įvykdytas."
    if me["employee"]["id"] != actor.id:
        return f"Šį dokumentą turi pasirašyti {me['employee']['name']}."
    waiting = next(s for s in doc_view["signers"] if s["status"] != "signed")
    return f"Dar ne jūsų eilė: laukiama {waiting['employee']['name']} parašo."


def fetch_pdf(conn, actor: people.Actor, doc_id: int, signed: bool) -> tuple[bytes, str]:
    doc = conn.execute(select(hr_documents).where(hr_documents.c.id == doc_id)).mappings().first()
    if doc is None:
        raise not_found("Dokumentas nerastas")
    get_visible_request(conn, actor, doc["request_id"])
    number = documents.request_number(doc["request_id"], conn.execute(
        select(hr_requests.c.created_at).where(hr_requests.c.id == doc["request_id"])).scalar_one())
    if not signed:
        return bytes(doc["pdf"]), f"{number}-{doc['kind']}.pdf"
    if doc["signed_pdf"] is None:
        raise not_found("Pasirašytas dokumentas dar nepasirengęs")
    return bytes(doc["signed_pdf"]), f"{number}-{doc['kind']}-pasirasytas.pdf"


def parse_month(month: str | None) -> tuple[int, int]:
    try:
        year, mon = (month or clock.today().strftime("%Y-%m")).split("-")
        first = date(int(year), int(mon), 1)
    except (ValueError, TypeError):
        raise bad_request("Neteisingas mėnuo (formatas YYYY-MM).") from None
    return first.year, first.month


def timesheet(conn, actor: people.Actor, month: str | None) -> dict:
    year, mon = parse_month(month)
    emps = people.employees_map(conn)
    if actor.is_hr:
        people_list = list(emps.values())
    elif actor.is_manager:
        visible = {actor.id, *people.subordinate_tree_ids(conn, actor.id)}
        people_list = [e for e in emps.values() if e["id"] in visible]
    else:
        raise forbidden("Tabelį mato tik vadovai ir personalo (HR) darbuotojai.")
    first, last = date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])
    marks: dict[int, dict[str, str]] = {}
    for r in conn.execute(select(hr_marks).where(hr_marks.c.day >= first, hr_marks.c.day <= last)).mappings():
        marks.setdefault(r["employee_id"], {})[r["day"].isoformat()] = r["mark"]
    days = [{"date": d.isoformat(), "day": d.day, "weekday": d.weekday(), "off": not workdays.is_workday(d),
             "holiday": workdays.holiday_name(d)} for d in workdays.days_between(first, last)]
    rows = [{**_short(e), "marks": marks.get(e["id"], {})} for e in sorted(people_list, key=lambda e: e["name"])]
    return {"month": f"{year:04d}-{mon:02d}", "days": days, "employees": rows}


_AUTH_LINK = re.compile(r"(?:https?://[^\s]+)?/hr/(?:login|sign)/[^\s\"'<>]+")
REDACTED = "[nuoroda paslėpta]"


def outbox(conn, actor: people.Actor) -> list[dict]:
    """Pašto dėžutė. Prisijungimo / pasirašymo nuorodos (su žetonais) rodomos TIK pačiam gavėjui:
    HR matydamas svetimus laiškus gauna link=None ir užmaskuotą tekstą (kitaip galėtų prisijungti kito vardu)."""
    query = select(hr_outbox).order_by(hr_outbox.c.id.desc()).limit(OUTBOX_LIMIT)
    if not actor.is_hr:
        query = query.where(hr_outbox.c.to_employee_id == actor.id)
    emps = people.employees_map(conn)
    out = []
    for m in conn.execute(query).mappings():
        own = m["to_employee_id"] == actor.id
        out.append({"id": m["id"], "ts": clock.iso(m["ts"]), "to": _short(emps.get(m["to_employee_id"])),
                    "to_email": m["to_email"], "kind": m["kind"], "subject": m["subject"],
                    "body": m["body"] if own else _AUTH_LINK.sub(REDACTED, m["body"] or ""),
                    "link": m["link"] if own else None, "sent_via": m["sent_via"]})
    return out


def me(conn, actor: people.Actor) -> dict:
    emp = actor.employee
    return {"employee": people.public(emp) | {"manager_id": emp["manager_id"], "company": emp["company"]},
            "roles": actor.roles, "csrf": actor.csrf, "balance": round(float(emp["balance"]), 1),
            "balance_available": service.available_balance(conn, emp)}
