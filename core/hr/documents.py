"""Dokumentai: PDF užšaldymas (sha256), pasirašymo objekto sukūrimas, pasirašyto PDF surinkimas."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import insert, select

from core.hr import clock, pdf, signing
from core.hr.schema import hr_documents, hr_signers

COMPANY = "Demo įmonė, UAB"


def request_number(request_id: int, created_ts: float) -> str:
    return f"HR-{clock.pretty(created_ts)[:4]}-{request_id:04d}"


def _base_payload(req: dict, emps: dict[int, dict]) -> dict:
    employee = emps[req["employee_id"]]
    return {
        "company": COMPANY,
        "number": request_number(req["id"], req["created_at"]),
        "created": clock.pretty(req["created_at"]),
        "employee": {"name": employee["name"], "position": employee["position"],
                     "department": employee["department"]},
        "manager": emps[employee["manager_id"]]["name"],
        "date_from": req["date_from"].isoformat(), "date_to": req["date_to"].isoformat(),
        "work_days": req["work_days"], "destination": req["destination"] or "", "purpose": req["purpose"] or "",
    }


def request_payload(req: dict, emps: dict[int, dict]) -> dict:
    kind = req["leave_kind"]
    title = "Prašymas dėl komandiruotės" if req["type"] == "trip" else \
        f"Prašymas: {pdf.LEAVE_LABELS[kind].lower()}"
    return {**_base_payload(req, emps), "kind": "request", "type": req["type"], "title": title,
            "leave_kind_label": pdf.LEAVE_LABELS.get(kind, ""), "comment": req["comment"] or "",
            "substitute": emps[req["substitute_id"]]["name"]}


def order_payload(req: dict, emps: dict[int, dict], created_ts: float) -> dict:
    base = _base_payload(req, emps)
    return {**base, "kind": "order", "title": "Įsakymas dėl komandiruotės", "created": clock.pretty(created_ts),
            "request_number": base["number"], "number": base["number"].replace("HR-", "ISK-")}


def create_document(conn, *, request_id: int, kind: str, payload: dict,
                    signer_specs: list[tuple[str, int]], emps: dict[int, dict]) -> int:
    """Sugeneruoja ir užšaldo PDF, sukuria pasirašymą (vienas dokumentas, keli pasirašantieji su eile)."""
    original = pdf.render(payload)
    signers = [signing.Signer(f"s{i}", emps[emp_id]["name"], emps[emp_id]["email"], i)
               for i, (_, emp_id) in enumerate(signer_specs, start=1)]
    token = signing.get_provider().create_signing(name=payload["title"], pdf=original, signers=signers,
                                                  postback_url="/api/hr/postback")
    doc_id = conn.execute(insert(hr_documents).values(
        request_id=request_id, kind=kind, title=payload["title"], pdf=original,
        sha256=hashlib.sha256(original).hexdigest(), payload=json.dumps(payload, ensure_ascii=False),
        signing_token=token, status="pending", created_at=clock.now())).inserted_primary_key[0]
    for signer, (role, emp_id) in zip(signers, signer_specs):
        conn.execute(insert(hr_signers).values(document_id=doc_id, signer_id=signer.id, employee_id=emp_id,
                                               role=role, sequence=signer.sequence, status="pending"))
    return doc_id


def verify_hash(doc: dict) -> bool:
    """Dokumentas po sukūrimo nekeičiamas: baitų sha256 turi sutapti su užšaldytu."""
    return hashlib.sha256(bytes(doc["pdf"])).hexdigest() == doc["sha256"]


def verify_integrity(doc: dict) -> bool:
    """Saugomi baitai sutampa su užšaldytu sha256 IR iš payload perpiešiamas originalas duoda tą patį sha256.

    Antra patikra reikalinga, nes pasirašytas PDF perpiešiamas iš payload (build_signed_pdf).
    """
    if not verify_hash(doc):
        return False
    return hashlib.sha256(pdf.render(json.loads(doc["payload"]))).hexdigest() == doc["sha256"]


def build_signed_pdf(doc: dict, signers: list[dict], emps: dict[int, dict]) -> bytes:
    signatures = [{"role_label": pdf.ROLE_LABELS[s["role"]], "name": emps[s["employee_id"]]["name"],
                   "signed_at": clock.pretty(s["signed_at"]), "method": s["method"] or signing.DEMO_METHOD}
                  for s in sorted(signers, key=lambda s: s["sequence"]) if s["status"] == "signed"]
    return pdf.render(json.loads(doc["payload"]), signatures=signatures, sha256=doc["sha256"])


def load_signers(conn, document_id: int) -> list[dict]:
    rows = conn.execute(select(hr_signers).where(hr_signers.c.document_id == document_id)
                        .order_by(hr_signers.c.sequence)).mappings().all()
    return [dict(r) for r in rows]
