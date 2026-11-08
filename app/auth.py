"""Prisijungimas vietiniam (on-prem) UNO apskaitos serveriui: ~5 naudotojai, SQLite, jokių išorinių servisų.

Integracija (app/main.py):
    from app import auth
    auth.settings.secure_cookie = False        # True, kai už HTTPS
    app.include_router(auth.router)
    # apsaugoti maršrutą:  user: auth.User = Depends(auth.require_role("accountant", "admin"))

Naudotojo sukūrimas:  py -3.12 -m app.auth create-user --company 1 --username vardas --role accountant

Rate limit laikomas atmintyje (vienas procesas). Perkrovus serverį skaitliukai nunulinami; 5 naudotojų
vietiniam diegimui to pakanka.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "uno.db"
ROLES = ("accountant", "manager", "admin")
MIN_PASSWORD_LEN = 12
SESSION_IDLE_SECONDS = 12 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
COOKIE_NAME = "uno_session"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
SCRYPT_N, SCRYPT_R, SCRYPT_P, SALT_LEN = 2**14, 8, 1, 16

log = logging.getLogger("uno.auth")

MSG_BAD_LOGIN = "Neteisingas naudotojo vardas arba slaptažodis"
MSG_RATE_LIMIT = "Per daug nesėkmingų bandymų. Bandykite vėliau (po 15 min.)"
MSG_NEED_LOGIN = "Reikia prisijungti"
MSG_FORBIDDEN = "Nepakanka teisių šiam veiksmui"
MSG_CSRF = "Neteisingas arba trūkstamas CSRF raktas"


class Settings:
    """Keičiama konfigūracija (testuose ir main.py). db_path=None: imama UNO_DB arba numatytoji."""
    db_path: Path | str | None = None
    secure_cookie: bool = os.environ.get("UNO_SECURE_COOKIE") == "1"  # VPS už HTTPS: UNO_SECURE_COOKIE=1


settings = Settings()


def db_path() -> Path:
    """Vienas DB kelias auth, API ir CLI: settings.db_path > aplinkos UNO_DB > data/uno.db."""
    return Path(settings.db_path or os.environ.get("UNO_DB") or DEFAULT_DB)


def normalize_username(username: str) -> str:
    """Vienodas vardas DB paieškai, ribojimo raktui ir naudotojo kūrimui."""
    return username.strip().casefold()


_failures: dict[tuple[str, str], list[float]] = {}   # (vardas, IP) -> nesėkmių laikai; riboto dydžio (MAX_TRACKED)
_ip_failures: dict[str, list[float]] = {}             # IP -> nesėkmių laikai (bendras IP limitas prieš slaptažodžio hash)
MAX_TRACKED = 10_000
MAX_IP_FAILURES = 30
MAX_USERNAME_LEN = 128
MAX_PASSWORD_LEN = 1024
MSG_LOGIN_FIELDS = "Per ilgas naudotojo vardas arba slaptažodis"


def now() -> float:
    return time.time()


@dataclass(frozen=True)
class User:
    id: int
    company_id: int
    username: str
    role: str
    csrf: str


# ---------- DB ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('accountant','manager','admin')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


def connect(db_file: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_file) if db_file else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_username_norm(conn)
    return conn


def _migrate_username_norm(conn: sqlite3.Connection) -> None:
    """username_norm (Python casefold) + UNIQUE indeksas. Kolizijų (pvz. 'Ana' ir 'ana') nesujungiame:
    visos susidūrusios paskyros išjungiamos, o administratorius gauna įspėjimą."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "username_norm" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN username_norm TEXT")
    pending = conn.execute("SELECT id, username FROM users WHERE username_norm IS NULL ORDER BY id").fetchall()
    if pending:
        existing = {r[0] for r in conn.execute("SELECT username_norm FROM users WHERE username_norm IS NOT NULL")}
        groups: dict[str, list[int]] = {}
        for row in pending:
            groups.setdefault(normalize_username(row["username"]), []).append(row["id"])
        for norm, ids in groups.items():
            if len(ids) == 1 and norm not in existing:
                conn.execute("UPDATE users SET username_norm=? WHERE id=?", (norm, ids[0]))
                continue
            for user_id in ids:
                conn.execute("UPDATE users SET username_norm=?, active=0 WHERE id=?",
                             (f"{norm}#dubliuotas#{user_id}", user_id))
            msg = (f"ĮSPĖJIMAS: naudotojų vardai '{norm}' sutampa po normalizavimo (paskyrų ID: "
                   f"{', '.join(map(str, ids))}). Šios paskyros IŠJUNGTOS; administratoriau, sukurkite jas iš naujo "
                   "skirtingais vardais.")
            log.warning(msg)
            print(msg, file=sys.stderr)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_norm ON users(username_norm)")
    conn.commit()


# ---------- slaptažodžiai ----------

def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Slaptažodis turi būti bent {MIN_PASSWORD_LEN} simbolių")
    salt = secrets.token_bytes(SALT_LEN)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(_scrypt(password, salt)).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, hash_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
    except ValueError:
        return False
    return hmac.compare_digest(_scrypt(password, salt), expected)


_DUMMY_HASH = hash_password("neegzistuojantis-naudotojas")


def create_user(conn: sqlite3.Connection, company_id: int, username: str, password: str, role: str) -> int:
    if role not in ROLES:
        raise ValueError(f"Rolė turi būti viena iš: {', '.join(ROLES)}")
    username = normalize_username(username)
    if not username:
        raise ValueError("Naudotojo vardas negali būti tuščias")
    cur = conn.execute(
        "INSERT INTO users(company_id, username, username_norm, password_hash, role, active, created_at)"
        " VALUES (?,?,?,?,?,1,?)",
        (company_id, username, username, hash_password(password), role, now()),
    )
    conn.commit()
    return cur.lastrowid


# ---------- rate limit ----------

def _prune(store: dict, cutoff: float) -> None:
    """Ištrina pasenusius įrašus; jei vis tiek per daug, išmeta seniausius (bounded TTL)."""
    for key in [k for k, v in store.items() if not v or v[-1] <= cutoff]:
        del store[key]
    if len(store) >= MAX_TRACKED:
        for key in sorted(store, key=lambda k: store[k][-1])[: len(store) - MAX_TRACKED + MAX_TRACKED // 10]:
            del store[key]


def _recent(store: dict, key) -> list[float]:
    cutoff = now() - FAILURE_WINDOW_SECONDS
    kept = [t for t in store.get(key, []) if t > cutoff]
    if kept:
        store[key] = kept
    else:
        store.pop(key, None)
    return kept


def is_ip_limited(ip: str) -> bool:
    return len(_recent(_ip_failures, ip)) >= MAX_IP_FAILURES


def is_rate_limited(username: str, ip: str) -> bool:
    return len(_recent(_failures, (normalize_username(username), ip))) >= MAX_FAILURES


def record_failure(username: str, ip: str) -> None:
    t = now()
    key = (normalize_username(username)[:MAX_USERNAME_LEN], ip)
    for store, k in ((_failures, key), (_ip_failures, ip)):
        if k not in store and len(store) >= MAX_TRACKED:
            _prune(store, t - FAILURE_WINDOW_SECONDS)
        store.setdefault(k, [])
        store[k] = _recent(store, k) + [t]


def reset_failures(username: str | None = None, ip: str | None = None) -> None:
    """Be argumentų nunulina viską (testams); su argumentais tik vieną porą (po sėkmingo prisijungimo)."""
    if username is None:
        _failures.clear()
        _ip_failures.clear()
    else:
        _failures.pop((normalize_username(username), ip or ""), None)


# ---------- sesijos ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    t = now()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                 (_hash_token(token), user_id, csrf, t, t + SESSION_IDLE_SECONDS))
    conn.commit()
    return token, csrf


def load_user(conn: sqlite3.Connection, token: str) -> User | None:
    """Grąžina naudotoją ir pratęsia 12 val. neveiklumo laiką; None, jei sesija negalioja."""
    row = conn.execute(
        "SELECT u.id, u.company_id, u.username, u.role, s.csrf, s.expires_at FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND u.active = 1",
        (_hash_token(token),),
    ).fetchone()
    if row is None or row["expires_at"] < now():
        return None
    conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                 (now() + SESSION_IDLE_SECONDS, _hash_token(token)))
    conn.commit()
    return User(row["id"], row["company_id"], row["username"], row["role"], row["csrf"])


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    conn.commit()


# ---------- FastAPI priklausomybės ----------

def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, MSG_NEED_LOGIN)
    with closing(connect()) as conn:
        user = load_user(conn, token)
    if user is None:
        raise HTTPException(401, MSG_NEED_LOGIN)
    return user


def require_csrf(request: Request, user: User = Depends(current_user)) -> User:
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), user.csrf.encode()):
            raise HTTPException(403, MSG_CSRF)
    return user


def require_role(*roles: str):
    """Priklausomybė: prisijungęs + CSRF (POST/PUT/DELETE) + viena iš nurodytų rolių."""
    def dependency(user: User = Depends(require_csrf)) -> User:
        if user.role not in roles:
            raise HTTPException(403, MSG_FORBIDDEN)
        return user
    return dependency


# ---------- maršrutai ----------

router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field(max_length=MAX_USERNAME_LEN)
    password: str = Field(max_length=MAX_PASSWORD_LEN)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = _client_ip(request)
    if is_ip_limited(ip) or is_rate_limited(body.username, ip):   # prieš scrypt: pigu ir neauga atmintis
        raise HTTPException(429, MSG_RATE_LIMIT)
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM users WHERE username_norm = ? AND active = 1",
                           (normalize_username(body.username),)).fetchone()
        ok = verify_password(body.password, row["password_hash"] if row else _DUMMY_HASH)
        if not (row and ok):
            record_failure(body.username, ip)
            raise HTTPException(401, MSG_BAD_LOGIN)
        token, csrf = create_session(conn, row["id"])
    reset_failures(body.username, ip)
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_IDLE_SECONDS, httponly=True,
                        samesite="strict", secure=settings.secure_cookie, path="/")
    return {"username": row["username"], "role": row["role"], "company_id": row["company_id"], "csrf": csrf}


@router.post("/api/logout")
def logout(request: Request, response: Response, user: User = Depends(require_csrf)):
    with closing(connect()) as conn:
        delete_session(conn, request.cookies[COOKIE_NAME])
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role, "company_id": user.company_id, "csrf": user.csrf}


# ---------- CLI ----------

def _cli_create_user(args: argparse.Namespace) -> int:
    password = getpass.getpass("Slaptažodis (min. 12 simbolių): ")
    if password != getpass.getpass("Pakartokite slaptažodį: "):
        print("Slaptažodžiai nesutampa", file=sys.stderr)
        return 1
    try:
        with closing(connect(args.db)) as conn:
            create_user(conn, args.company, args.username, password, args.role)
    except (ValueError, sqlite3.IntegrityError) as e:
        print(f"Klaida: {e}", file=sys.stderr)
        return 1
    print(f"Naudotojas {args.username} sukurtas")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.auth")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user")
    p.add_argument("--company", type=int, required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--role", choices=ROLES, required=True)
    p.add_argument("--db", default=None, help="DB failas (nurodžius, viršija UNO_DB)")
    return _cli_create_user(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
