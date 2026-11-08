"""Prisijungimas: sesijos (cookie uno_hr_session, DB tik sha256) ir vienkartinės nuorodos (30 min.)."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import delete, func, insert, select, text, update

from core.hr import clock, db, notify, people
from core.hr.errors import forbidden
from core.hr.schema import hr_login_attempts, hr_magic_links, hr_sessions

COOKIE_NAME = "uno_hr_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_IDLE_SECONDS = 8 * 3600
MAGIC_LINK_SECONDS = 30 * 60
RATE_WINDOW = 15 * 60
RATE_PER_EMAIL, RATE_PER_IP = 5, 20
MSG_NEED_LOGIN = "Reikia prisijungti"
MSG_CSRF = "Neteisingas arba trūkstamas CSRF raktas"

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, employee_id: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    t = clock.now()
    conn.execute(delete(hr_sessions).where(hr_sessions.c.expires_at < t))
    conn.execute(insert(hr_sessions).values(token_hash=hash_token(token), employee_id=employee_id, csrf=csrf,
                                            created_at=t, expires_at=t + SESSION_IDLE_SECONDS))
    return token, csrf


def load_actor(conn, token: str) -> people.Actor | None:
    """Sesijos darbuotojas; pratęsia 8 val. neveiklumo laiką. None, jei sesija nebegalioja."""
    row = conn.execute(select(hr_sessions).where(hr_sessions.c.token_hash == hash_token(token))).mappings().first()
    if row is None or row["expires_at"] < clock.now():
        return None
    employee = people.get_employee(conn, row["employee_id"])
    if employee is None:
        return None
    conn.execute(update(hr_sessions).where(hr_sessions.c.token_hash == row["token_hash"])
                 .values(expires_at=clock.now() + SESSION_IDLE_SECONDS))
    return people.make_actor(conn, employee, row["csrf"])


def delete_session(conn, token: str) -> None:
    conn.execute(delete(hr_sessions).where(hr_sessions.c.token_hash == hash_token(token)))


def check_csrf(actor: people.Actor, sent: str) -> None:
    if not hmac.compare_digest(sent.encode(), actor.csrf.encode()):
        raise forbidden(MSG_CSRF)


# ---------- vienkartinės nuorodos ----------

def _count_attempts(conn, key: str) -> int:
    """Užregistruoja bandymą ir grąžina bandymų skaičių lange (insert + count vienoje transakcijoje)."""
    if conn.dialect.name == "postgresql":  # serializuoja to paties rakto bandymus tarp sesijų
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
    conn.execute(insert(hr_login_attempts).values(key=key, ts=clock.now()))
    return conn.execute(select(func.count()).select_from(hr_login_attempts).where(
        hr_login_attempts.c.key == key, hr_login_attempts.c.ts > clock.now() - RATE_WINDOW)).scalar_one()


def rate_limited(conn, email: str, ip: str) -> bool:
    """True, jei viršytas el. pašto arba IP limitas per 15 min. Bandymai saugomi DB (veikia serverless)."""
    conn.execute(delete(hr_login_attempts).where(hr_login_attempts.c.ts < clock.now() - RATE_WINDOW))
    email_key = "email:" + hashlib.sha256(email.encode()).hexdigest()[:40]
    by_ip = _count_attempts(conn, f"ip:{ip}"[:80]) > RATE_PER_IP
    by_email = _count_attempts(conn, email_key) > RATE_PER_EMAIL
    return by_ip or by_email


def reset_rate_limits() -> None:
    with db.tx() as conn:
        conn.execute(delete(hr_login_attempts))


def request_magic_link(conn, email: str, ip: str) -> None:
    """Visada tyliai: nepasakome, ar toks el. paštas egzistuoja. Nuoroda dedama į outbox."""
    email = email.strip().lower()
    if not email or rate_limited(conn, email, ip):
        return
    employee = people.find_by_email(conn, email)
    if employee is None:
        return
    token = secrets.token_urlsafe(32)
    t = clock.now()
    conn.execute(insert(hr_magic_links).values(token_hash=hash_token(token), employee_id=employee["id"],
                                               created_at=t, expires_at=t + MAGIC_LINK_SECONDS))
    notify.queue_mail(conn, employee, kind="login_link", subject="Prisijungimo nuoroda",
                      body="Jūsų vienkartinė prisijungimo nuoroda galioja 30 min.", link=f"/hr/login/{token}")


def consume_magic_link(conn, token: str) -> int | None:
    """Darbuotojo ID, jei nuoroda galioja ir nepanaudota; panaudojama atominiu UPDATE (vienkartinė)."""
    now = clock.now()
    res = conn.execute(update(hr_magic_links)
                       .where(hr_magic_links.c.token_hash == hash_token(token), hr_magic_links.c.used_at.is_(None),
                              hr_magic_links.c.expires_at >= now)
                       .values(used_at=now))
    if res.rowcount != 1:
        return None
    return conn.execute(select(hr_magic_links.c.employee_id)
                        .where(hr_magic_links.c.token_hash == hash_token(token))).scalar_one()
