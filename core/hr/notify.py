"""Audito žurnalas ir „Pašto dėžutė (demo)“ (hr_outbox). Realus siuntimas per Resend tik allowlist adresams."""
from __future__ import annotations

import logging
import os

import httpx
from sqlalchemy import insert, update

from core.hr import clock, db
from core.hr.schema import hr_audit, hr_outbox

log = logging.getLogger("uno.hr.notify")
RESEND_URL = "https://api.resend.com/emails"
LOGIN_KIND = "login_link"


def audit(conn, request_id: int, actor_id: int | None, action: str, detail: str,
          document_id: int | None = None) -> None:
    conn.execute(insert(hr_audit).values(request_id=request_id, document_id=document_id, ts=clock.now(),
                                         actor_id=actor_id, action=action, detail=detail))


def queue_mail(conn, employee: dict, *, kind: str, subject: str, body: str, link: str | None = None) -> int:
    """Įrašo laišką į outbox (visada) ir po commit bando išsiųsti per Resend, jei adresas leidžiamas.

    Gamyboje (HR_DEMO!=1) prisijungimo nuoroda į outbox / DB NEįrašoma: žetonas keliauja tik atmintyje į
    Resend. Be sukonfigūruoto pašto (RESEND_API_KEY + HR_MAIL_ALLOWLIST) nuoroda niekur nerodoma."""
    stored_link = None if kind == LOGIN_KIND and os.environ.get("HR_DEMO") != "1" else link
    res = conn.execute(insert(hr_outbox).values(
        ts=clock.now(), to_employee_id=employee["id"], to_email=employee["email"], kind=kind,
        subject=subject, body=body, link=stored_link, sent_via="outbox"))
    mail_id = res.inserted_primary_key[0]
    ctx = db.conn_ctx(conn)
    base = ctx["base_url"]
    ctx["after_commit"].append(lambda: deliver(mail_id, employee["email"], subject, body, link, base))
    return mail_id


def allowlist() -> set[str]:
    return {a.strip().lower() for a in os.environ.get("HR_MAIL_ALLOWLIST", "").split(",") if a.strip()}


def absolute(link: str | None, base_url: str) -> str:
    base = (os.environ.get("HR_BASE_URL") or base_url or "").rstrip("/")
    return f"{base}{link}" if link else ""


def deliver(mail_id: int, to_email: str, subject: str, body: str, link: str | None, base_url: str) -> bool:
    """Resend tik kai nustatyti RESEND_API_KEY ir HR_MAIL_ALLOWLIST ir gavėjas sąraše. Klaidos nemeta."""
    key = os.environ.get("RESEND_API_KEY")
    if not key or to_email.lower() not in allowlist():
        return False
    text = body + (f"\n\n{absolute(link, base_url)}" if link else "")
    sender = os.environ.get("HR_MAIL_FROM", "Demo įmonė <onboarding@resend.dev>")
    try:
        resp = httpx.post(RESEND_URL, headers={"Authorization": f"Bearer {key}"}, timeout=10,
                          json={"from": sender, "to": [to_email], "subject": subject, "text": text})
        resp.raise_for_status()
    except httpx.HTTPError:
        log.exception("Resend siuntimas nepavyko (laiškas lieka outbox)")
        return False
    with db.get_engine().begin() as conn:
        conn.execute(update(hr_outbox).where(hr_outbox.c.id == mail_id).values(sent_via="resend"))
    return True
