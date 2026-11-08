"""Pasirašymo eiga: postback apdorojimas (tas pats kelias, kurį kviestų tikras Dokobit), būsenos ir pasekmės.

Viskas vienoje DB transakcijoje (db.tx). Idempotentiška: pakartotinis įvykis nieko nekeičia.
Pasekmės (likutis, tabelio žymėjimai) taikomos tik vieną kartą per hr_requests.effects_applied.
"""
from __future__ import annotations

from sqlalchemy import insert, select, update

from core.hr import clock, documents, notify, people, service, signing, workdays
from core.hr.errors import bad_request, conflict, forbidden, not_found
from core.hr.schema import hr_documents, hr_marks, hr_requests, hr_signers

EVENTS = ("signer_signed", "signer_declined", "signing_completed")
MARKS = {"annual": "A", "unpaid": "NA", "study": "MA"}
MIN_REASON = 3


def _lock_document(conn, token: str) -> dict:
    row = conn.execute(select(hr_documents).where(hr_documents.c.signing_token == token)
                       .with_for_update()).mappings().first()
    if row is None:
        raise not_found("Pasirašymas nerastas")
    return dict(row)


def _get_signer(conn, doc: dict, signer_id: str) -> dict:
    row = conn.execute(select(hr_signers).where(hr_signers.c.document_id == doc["id"],
                                                hr_signers.c.signer_id == signer_id)).mappings().first()
    if row is None:
        raise not_found("Pasirašantysis nerastas")
    return dict(row)


def _request(conn, request_id: int) -> dict:
    return dict(conn.execute(select(hr_requests).where(hr_requests.c.id == request_id)).mappings().one())


def _result(conn, doc_id: int, changed: bool) -> dict:
    status = conn.execute(select(hr_documents.c.status).where(hr_documents.c.id == doc_id)).scalar_one()
    return {"ok": True, "changed": changed, "document_status": status}


def handle_postback(conn, *, token: str, signer_id: str | None, event: str, reason: str | None = None,
                    actor_id: int | None = None) -> dict:
    """Vienintelis būdas keisti pasirašymo būseną. Demo sign() ir tikras Dokobit webhook'as kviečia tą patį."""
    if event not in EVENTS:
        raise bad_request("Nežinomas pasirašymo įvykis.")
    doc = _lock_document(conn, token)
    if event == "signing_completed":
        return _completed(conn, doc, actor_id)
    signer = _get_signer(conn, doc, signer_id or "")
    if event == "signer_signed":
        return _signed(conn, doc, signer, actor_id)
    return _declined(conn, doc, signer, reason, actor_id)


def _require_turn(conn, doc: dict, signer: dict) -> None:
    if doc["status"] != "pending":
        raise conflict("Dokumentas nebegalioja arba jau užbaigtas.")
    waiting = [s for s in documents.load_signers(conn, doc["id"])
               if s["sequence"] < signer["sequence"] and s["status"] != "signed"]
    if waiting:
        raise conflict("Dar ne jūsų eilė pasirašyti.")


def _signed(conn, doc: dict, signer: dict, actor_id: int | None) -> dict:
    if signer["status"] == "signed":
        return _result(conn, doc["id"], False)
    if signer["status"] != "pending":
        raise conflict("Šis pasirašantysis jau atsisakė.")
    _require_turn(conn, doc, signer)
    _require_integrity(conn, doc, actor_id)
    conn.execute(update(hr_signers).where(hr_signers.c.id == signer["id"]).values(
        status="signed", signed_at=clock.now(), method=signing.DEMO_METHOD, signed_sha256=doc["sha256"]))
    emps = people.employees_map(conn)
    notify.audit(conn, doc["request_id"], signer["employee_id"], "signed",
                 f"{emps[signer['employee_id']]['name']} pasirašė ({_role(signer)})", doc["id"])
    pending = [s for s in documents.load_signers(conn, doc["id"]) if s["status"] != "signed"]
    if pending:
        _invite(conn, doc, pending[0], emps)
        return _result(conn, doc["id"], True)
    return _completed(conn, doc, actor_id)


def _require_integrity(conn, doc: dict, actor_id: int | None) -> None:
    """Prieš parašą ir prieš pasirašyto PDF surinkimą: originalas turi sutapti su užšaldytu sha256."""
    if documents.verify_integrity(doc):
        return
    notify.audit(conn, doc["request_id"], actor_id, "tamper_detected",
                 "Dokumento kontrolinė suma nesutampa, pasirašymas sustabdytas", doc["id"])
    raise conflict("Dokumento kontrolinė suma nesutampa. Pasirašymas sustabdytas.", commit=True)


def _role(signer: dict) -> str:
    return {"employee": "darbuotojas", "substitute": "pavaduojantis", "manager": "vadovas"}[signer["role"]]


def _invite(conn, doc: dict, signer: dict, emps: dict[int, dict]) -> None:
    req = _request(conn, doc["request_id"])
    who = emps[req["employee_id"]]["name"]
    link = signing.get_provider().signer_url(doc["signing_token"], signer["signer_id"])
    subject = "Įsakymas laukia jūsų parašo" if doc["kind"] == "order" else "Prašymas laukia jūsų parašo"
    notify.queue_mail(conn, emps[signer["employee_id"]], kind="sign_invite", subject=subject, link=link,
                      body=f"{doc['title']}: {who}, {req['date_from']} iki {req['date_to']}.")


def _declined(conn, doc: dict, signer: dict, reason: str | None, actor_id: int | None) -> dict:
    if signer["status"] == "declined":
        return _result(conn, doc["id"], False)
    if signer["status"] != "pending":
        raise conflict("Šis pasirašantysis jau pasirašė.")
    _require_turn(conn, doc, signer)
    reason = (reason or "").strip()
    conn.execute(update(hr_signers).where(hr_signers.c.id == signer["id"]).values(status="declined", reason=reason))
    conn.execute(update(hr_documents).where(hr_documents.c.id == doc["id"]).values(status="declined"))
    emps = people.employees_map(conn)
    name = emps[signer["employee_id"]]["name"]
    if doc["kind"] == "order":
        conn.execute(update(hr_requests).where(hr_requests.c.id == doc["request_id"]).values(
            order_status="order_rejected", status="rejected", reason=reason, declined_by=signer["employee_id"]))
    else:
        conn.execute(update(hr_requests).where(hr_requests.c.id == doc["request_id"]).values(
            status="rejected", reason=reason, declined_by=signer["employee_id"]))
    notify.audit(conn, doc["request_id"], signer["employee_id"], "declined", f"{name} atsisakė: {reason}", doc["id"])
    owner = emps[_request(conn, doc["request_id"])["employee_id"]]
    notify.queue_mail(conn, owner, kind="declined", subject="Prašymas atmestas",
                      body=f"{name} atsisakė pasirašyti ({doc['title']}). Priežastis: {reason}")
    return _result(conn, doc["id"], True)


def _completed(conn, doc: dict, actor_id: int | None) -> dict:
    if doc["status"] == "completed":
        return _result(conn, doc["id"], False)
    if doc["status"] != "pending":
        raise conflict("Dokumentas nebegalioja.")
    signers = documents.load_signers(conn, doc["id"])
    if any(s["status"] != "signed" for s in signers):
        raise conflict("Ne visi pasirašė.")
    _require_integrity(conn, doc, actor_id)
    emps = people.employees_map(conn)
    signed_pdf = documents.build_signed_pdf(doc, signers, emps)
    conn.execute(update(hr_documents).where(hr_documents.c.id == doc["id"]).values(status="completed",
                                                                                   signed_pdf=signed_pdf))
    req = _request(conn, doc["request_id"])
    if doc["kind"] == "request":
        _approve_request(conn, req, doc, emps)
    else:
        _order_signed(conn, req, doc, emps)
    return _result(conn, doc["id"], True)


def _claim_effects(conn, request_id: int) -> bool:
    """Atominis „pasekmės taikomos tik vieną kartą“: UPDATE ... WHERE effects_applied = false."""
    res = conn.execute(update(hr_requests).where(hr_requests.c.id == request_id,
                                                 hr_requests.c.effects_applied.is_(False)).values(effects_applied=True))
    return res.rowcount == 1


def _approve_request(conn, req: dict, doc: dict, emps: dict[int, dict]) -> None:
    conn.execute(update(hr_requests).where(hr_requests.c.id == req["id"]).values(status="approved"))
    notify.audit(conn, req["id"], None, "approved", "Prašymas patvirtintas (pasirašė visi)", doc["id"])
    owner = emps[req["employee_id"]]
    if req["type"] == "leave":
        if _claim_effects(conn, req["id"]):
            _apply_leave(conn, req)
        notify.queue_mail(conn, owner, kind="approved", subject="Prašymas patvirtintas",
                          body=f"Jūsų prašymas {req['date_from']} iki {req['date_to']} patvirtintas.")
        return
    _start_order(conn, req, emps)
    notify.queue_mail(conn, owner, kind="approved", subject="Prašymas patvirtintas",
                      body="Komandiruotės prašymas patvirtintas. Vadovas pasirašys įsakymą.")


def _apply_leave(conn, req: dict) -> None:
    if req["leave_kind"] == "annual":
        service.adjust_balance(conn, req["employee_id"], -float(req["work_days"]))
        notify.audit(conn, req["id"], None, "balance_decreased", f"Likutis sumažintas {req['work_days']} d.")
    _add_marks(conn, req, MARKS[req["leave_kind"]])


def _add_marks(conn, req: dict, mark: str) -> None:
    taken = {r[0] for r in conn.execute(select(hr_marks.c.day).where(
        hr_marks.c.employee_id == req["employee_id"], hr_marks.c.day >= req["date_from"],
        hr_marks.c.day <= req["date_to"]))}
    days = [d for d in workdays.iter_workdays(req["date_from"], req["date_to"]) if d not in taken]
    for day in days:
        conn.execute(insert(hr_marks).values(employee_id=req["employee_id"], day=day, mark=mark,
                                             request_id=req["id"]))
    notify.audit(conn, req["id"], None, "timesheet_marked", f"Tabelyje pažymėta {len(days)} d. ({mark})")


def _start_order(conn, req: dict, emps: dict[int, dict]) -> None:
    manager_id = emps[req["employee_id"]]["manager_id"]
    payload = documents.order_payload(req, emps, clock.now())
    doc_id = documents.create_document(conn, request_id=req["id"], kind="order", payload=payload,
                                       signer_specs=[("manager", manager_id)], emps=emps)
    conn.execute(update(hr_requests).where(hr_requests.c.id == req["id"]).values(order_status="pending_order"))
    notify.audit(conn, req["id"], None, "order_created", "Sugeneruotas komandiruotės įsakymas", doc_id)
    order = dict(conn.execute(select(hr_documents).where(hr_documents.c.id == doc_id)).mappings().one())
    _invite(conn, order, documents.load_signers(conn, doc_id)[0], emps)


def _order_signed(conn, req: dict, doc: dict, emps: dict[int, dict]) -> None:
    conn.execute(update(hr_requests).where(hr_requests.c.id == req["id"]).values(order_status="order_signed"))
    notify.audit(conn, req["id"], None, "order_signed", "Įsakymas pasirašytas", doc["id"])
    if _claim_effects(conn, req["id"]):
        _add_marks(conn, req, "K")
    notify.queue_mail(conn, emps[req["employee_id"]], kind="order_signed", subject="Komandiruotės įsakymas pasirašytas",
                      body=f"Įsakymas dėl komandiruotės {req['date_from']} iki {req['date_to']} pasirašytas.")


def sign(conn, actor: people.Actor, token: str, signer_id: str, action: str, reason: str | None) -> int:
    """Demo pasirašymas: tik prisijungęs pasirašantysis; toliau tas pats postback kelias. Grąžina prašymo ID."""
    doc = _lock_document(conn, token)
    signer = _get_signer(conn, doc, signer_id)
    req = _request(conn, doc["request_id"])
    if signer["employee_id"] != actor.id:
        if not people.can_view(conn, actor, req):
            raise not_found("Pasirašymas nerastas")
        raise forbidden("Šį dokumentą turi pasirašyti kitas asmuo.")
    if action not in ("sign", "decline"):
        raise bad_request("Veiksmas turi būti sign arba decline.")
    if action == "decline" and len((reason or "").strip()) < MIN_REASON:
        raise bad_request("Nurodykite atsisakymo priežastį.")
    event = "signer_signed" if action == "sign" else "signer_declined"
    handle_postback(conn, token=token, signer_id=signer_id, event=event, reason=reason, actor_id=actor.id)
    return req["id"]
