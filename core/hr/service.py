"""Prašymų kūrimas, validacija ir atšaukimas. Pasirašymo eiga ir pasekmės: core/hr/flow.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, insert, select, update

from core.hr import clock, documents, notify, people, workdays
from core.hr.errors import bad_request, conflict, forbidden, not_found
from core.hr.schema import hr_documents, hr_employees, hr_requests, hr_signers

LEAVE_KINDS = ("annual", "unpaid", "study")
REQUEST_TYPES = ("leave", "trip")
ACTIVE_STATUSES = ("pending_signatures", "approved")
MAX_TEXT = 500
MAX_PLACE_TEXT = 200   # destination / purpose (VARCHAR(200) lentelėje)


@dataclass(frozen=True)
class NewRequest:
    type: str
    leave_kind: str | None
    date_from: date
    date_to: date
    substitute_id: int
    destination: str
    purpose: str
    comment: str


def fmt_days(value: float) -> str:
    """3.5 -> '3,5'; 5.0 -> '5' (lietuviškas kablelis žinutėse)."""
    return (f"{value:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def parse_date(value, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise bad_request(f"Neteisinga {label} (formatas YYYY-MM-DD).") from None


def parse_range(date_from, date_to) -> tuple[date, date]:
    start, end = parse_date(date_from, "pradžios data"), parse_date(date_to, "pabaigos data")
    if end < start:
        raise bad_request("Pabaigos data negali būti ankstesnė už pradžios datą.")
    if start < workdays.MIN_DATE or end > workdays.MAX_DATE or (end - start).days >= workdays.MAX_RANGE_DAYS:
        raise bad_request("Palaikomi tik 2026 ir 2027 m. laikotarpiai (iki 366 d.).")
    return start, end


def parse_input(data: dict) -> NewRequest:
    kind = data.get("type")
    if kind not in REQUEST_TYPES:
        raise bad_request("Pasirinkite prašymo tipą: atostogos arba komandiruotė.")
    leave_kind = (data.get("leave_kind") or "annual") if kind == "leave" else None
    if leave_kind is not None and leave_kind not in LEAVE_KINDS:
        raise bad_request("Neteisingas atostogų tipas.")
    start, end = parse_range(data.get("date_from"), data.get("date_to"))
    texts = {k: str(data.get(k) or "").strip() for k in ("destination", "purpose", "comment")}
    if any(len(texts[k]) > MAX_PLACE_TEXT for k in ("destination", "purpose")):
        raise bad_request(f"Kelionės tikslas ir paskirtis negali būti ilgesni nei {MAX_PLACE_TEXT} simbolių.")
    if len(texts["comment"]) > MAX_TEXT:
        raise bad_request(f"Komentaras per ilgas (daugiausia {MAX_TEXT} simbolių).")
    if kind == "trip" and not (texts["destination"] and texts["purpose"]):
        raise bad_request("Komandiruotei privalomi kelionės tikslas (vieta) ir paskirtis.")
    sub = data.get("substitute_id")
    if not isinstance(sub, int) or isinstance(sub, bool):
        raise bad_request("Pasirinkite pavaduojantį.")
    return NewRequest(kind, leave_kind, start, end, sub, texts["destination"], texts["purpose"], texts["comment"])


def reserved_days(conn, employee_id: int) -> float:
    """Kasmetinių atostogų dienos, kurios jau laukia parašų (rezervuotos, kad nepasiektume minuso)."""
    total = conn.execute(select(func.coalesce(func.sum(hr_requests.c.work_days), 0)).where(
        hr_requests.c.employee_id == employee_id, hr_requests.c.leave_kind == "annual",
        hr_requests.c.status == "pending_signatures")).scalar_one()
    return float(total)


def available_balance(conn, employee: dict) -> float:
    return round(float(employee["balance"]) - reserved_days(conn, employee["id"]), 1)


def check_substitute(actor: people.Actor, nr: NewRequest, emps: dict[int, dict]) -> None:
    if nr.substitute_id == actor.id:
        raise bad_request("Pavaduojantis negali būti pats darbuotojas.")
    if nr.substitute_id not in emps:
        raise bad_request("Pavaduojantis nerastas toje pačioje įmonėje.")
    manager_id = actor.employee["manager_id"]
    if nr.substitute_id == manager_id:
        raise bad_request("Pavaduojantis negali būti jūsų vadovas.")


def check_overlap(conn, employee_id: int, nr: NewRequest) -> None:
    clash = conn.execute(select(hr_requests.c.id).where(
        hr_requests.c.employee_id == employee_id, hr_requests.c.status.in_(ACTIVE_STATUSES),
        hr_requests.c.date_from <= nr.date_to, hr_requests.c.date_to >= nr.date_from)).first()
    if clash:
        raise conflict("Šiuo laikotarpiu jau yra aktyvus arba patvirtintas jūsų prašymas.")


def lock_employee(conn, employee_id: int) -> dict:
    """Užrakina darbuotojo eilutę (Postgres FOR NO KEY UPDATE, kad FK KEY SHARE nesukeltų deadlock; SQLite: BEGIN IMMEDIATE)."""
    row = conn.execute(select(hr_employees).where(hr_employees.c.id == employee_id)
                       .with_for_update(key_share=True)).mappings().one()
    return dict(row)


def validate(conn, actor: people.Actor, employee: dict, nr: NewRequest,
             emps: dict[int, dict]) -> tuple[int, list[str]]:
    check_substitute(actor, nr, emps)
    days = workdays.work_days(nr.date_from, nr.date_to)
    if days == 0:
        raise bad_request("Pasirinktu laikotarpiu nėra darbo dienų.")
    check_overlap(conn, actor.id, nr)
    if nr.leave_kind == "annual":
        left = available_balance(conn, employee)
        if days > left:
            raise bad_request(f"Nepakanka likučio: liko {fmt_days(left)} d.")
    warnings = ["Prašymas apima praėjusį laikotarpį."] if nr.date_from < clock.today() else []
    return days, warnings


def signer_specs(actor: people.Actor, substitute_id: int) -> list[tuple[str, int]]:
    """Eilė: darbuotojas, pavaduojantis, vadovas. Direktoriaus vadovas jis pats: pasirašo antrą kartą kaip vadovas."""
    return [("employee", actor.id), ("substitute", substitute_id), ("manager", actor.employee["manager_id"])]


def create_request(conn, actor: people.Actor, data: dict) -> tuple[int, list[str]]:
    emps = people.employees_map(conn)
    nr = parse_input(data)
    employee = lock_employee(conn, actor.id)  # serializuoja likučio ir persidengimo patikras to paties darbuotojo prašymams
    days, warnings = validate(conn, actor, employee, nr, emps)
    created = clock.now()
    request_id = conn.execute(insert(hr_requests).values(
        type=nr.type, leave_kind=nr.leave_kind, employee_id=actor.id, substitute_id=nr.substitute_id,
        date_from=nr.date_from, date_to=nr.date_to, work_days=days, destination=nr.destination or None,
        purpose=nr.purpose or None, comment=nr.comment or None, status="pending_signatures",
        effects_applied=False, created_at=created)).inserted_primary_key[0]
    req = dict(conn.execute(select(hr_requests).where(hr_requests.c.id == request_id)).mappings().one())
    doc_id = documents.create_document(conn, request_id=request_id, kind="request",
                                       payload=documents.request_payload(req, emps),
                                       signer_specs=signer_specs(actor, nr.substitute_id), emps=emps)
    doc = conn.execute(select(hr_documents.c.sha256).where(hr_documents.c.id == doc_id)).scalar_one()
    notify.audit(conn, request_id, actor.id, "created", f"Prašymas sukurtas ({days} darbo d.)", doc_id)
    notify.audit(conn, request_id, actor.id, "document_frozen", f"PDF užšaldytas, sha256 {doc}", doc_id)
    return request_id, warnings


def manager_signed(conn, request_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(hr_signers.join(
        hr_documents, hr_documents.c.id == hr_signers.c.document_id)).where(
        hr_documents.c.request_id == request_id, hr_documents.c.kind == "request",
        hr_signers.c.role == "manager", hr_signers.c.status == "signed")).scalar_one() > 0


def cancel_request(conn, actor: people.Actor, request_id: int) -> None:
    req = conn.execute(select(hr_requests).where(hr_requests.c.id == request_id).with_for_update()).mappings().first()
    if req is None or not people.can_view(conn, actor, dict(req)):
        raise not_found("Prašymas nerastas")
    if req["employee_id"] != actor.id:
        raise forbidden("Atšaukti gali tik prašymo autorius.")
    if req["status"] != "pending_signatures" or manager_signed(conn, request_id):
        raise conflict("Prašymo atšaukti nebegalima.")
    conn.execute(update(hr_requests).where(hr_requests.c.id == request_id).values(status="cancelled"))
    conn.execute(update(hr_documents).where(hr_documents.c.request_id == request_id,
                                            hr_documents.c.status == "pending").values(status="cancelled"))
    notify.audit(conn, request_id, actor.id, "cancelled", "Prašymą atšaukė darbuotojas")
    emps = people.employees_map(conn)
    for emp_id in {req["substitute_id"], emps[req["employee_id"]]["manager_id"]} - {actor.id}:
        notify.queue_mail(conn, emps[emp_id], kind="cancelled", subject="Prašymas atšauktas",
                          body=f"{actor.employee['name']} atšaukė prašymą {req['date_from']} iki {req['date_to']}.")


def adjust_balance(conn, employee_id: int, delta: float) -> None:
    conn.execute(update(hr_employees).where(hr_employees.c.id == employee_id)
                 .values(balance=hr_employees.c.balance + delta))
