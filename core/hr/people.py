"""Darbuotojai, rolės ir matomumo taisyklės (kas ką mato)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from core.hr.schema import hr_employees, hr_signers, hr_documents


@dataclass(frozen=True)
class Actor:
    """Prisijungęs darbuotojas (po sesijos tikrinimo)."""
    employee: dict
    csrf: str
    is_manager: bool

    @property
    def id(self) -> int:
        return self.employee["id"]

    @property
    def is_hr(self) -> bool:
        return bool(self.employee["is_hr"])

    @property
    def roles(self) -> list[str]:
        return ["employee"] + (["manager"] if self.is_manager else []) + (["hr"] if self.is_hr else [])


def employees_map(conn) -> dict[int, dict]:
    rows = conn.execute(select(hr_employees).where(hr_employees.c.active.is_(True))).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def get_employee(conn, employee_id: int) -> dict | None:
    row = conn.execute(select(hr_employees).where(hr_employees.c.id == employee_id,
                                                  hr_employees.c.active.is_(True))).mappings().first()
    return dict(row) if row else None


def find_by_email(conn, email: str) -> dict | None:
    row = conn.execute(select(hr_employees).where(hr_employees.c.email == email.strip().lower(),
                                                  hr_employees.c.active.is_(True))).mappings().first()
    return dict(row) if row else None


def subordinate_ids(conn, manager_id: int) -> list[int]:
    rows = conn.execute(select(hr_employees.c.id).where(hr_employees.c.manager_id == manager_id,
                                                        hr_employees.c.id != manager_id,
                                                        hr_employees.c.active.is_(True))).all()
    return [r[0] for r in rows]


def subordinate_tree_ids(conn, manager_id: int) -> list[int]:
    """Visi pavaldiniai rekursyviai (pavaldinių pavaldiniai), be paties vadovo."""
    rows = conn.execute(select(hr_employees.c.id, hr_employees.c.manager_id).where(
        hr_employees.c.active.is_(True))).all()
    children: dict[int, list[int]] = {}
    for emp_id, mgr_id in rows:
        if emp_id != mgr_id:
            children.setdefault(mgr_id, []).append(emp_id)
    seen: set[int] = {manager_id}
    stack, found = [manager_id], []
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return found


def make_actor(conn, employee: dict, csrf: str) -> Actor:
    return Actor(employee, csrf, bool(subordinate_ids(conn, employee["id"])))


def public(emp: dict | None) -> dict | None:
    if emp is None:
        return None
    return {"id": emp["id"], "name": emp["name"], "email": emp["email"], "position": emp["position"],
            "department": emp["department"]}


def request_signer_ids(conn, request_id: int) -> set[int]:
    rows = conn.execute(select(hr_signers.c.employee_id).join(hr_documents, hr_documents.c.id == hr_signers.c.document_id)
                        .where(hr_documents.c.request_id == request_id)).all()
    return {r[0] for r in rows}


def can_view(conn, actor: Actor, request: dict) -> bool:
    """Savininkas, pasirašantysis, savo pavaldinio vadovas arba HR."""
    if actor.is_hr or request["employee_id"] == actor.id:
        return True
    if actor.is_manager and request["employee_id"] in subordinate_ids(conn, actor.id):
        return True
    return actor.id in request_signer_ids(conn, request["id"])
