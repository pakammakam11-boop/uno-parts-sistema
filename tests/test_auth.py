"""Auth modulio testai: mažas FastAPI app su TestClient, laikinas SQLite."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth

PASSWORD = "labai-ilgas-slaptazodis"
WRONG = "neteisingas-slaptazodis"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.settings, "db_path", tmp_path / "uno.db")
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, "rivile", PASSWORD, "accountant")
        auth.create_user(conn, 1, "vadovas", PASSWORD, "manager")
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/api/secret")
    def secret(user=Depends(auth.require_role("accountant"))):
        return {"hi": user.username}

    @app.post("/api/change")
    def change(user=Depends(auth.require_role("accountant", "manager"))):
        return {"ok": True}

    return TestClient(app)


def login(client, username="rivile", password=PASSWORD):
    return client.post("/api/login", json={"username": username, "password": password})


def test_login_ok_sets_secure_cookie_flags(client):
    r = login(client)
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "accountant" and data["company_id"] == 1 and data["csrf"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/me").json()["username"] == "rivile"


def test_login_wrong_password(client):
    r = login(client, password=WRONG)
    assert r.status_code == 401
    assert r.json()["detail"] == auth.MSG_BAD_LOGIN
    assert client.get("/api/me").status_code == 401


def test_rate_limit_after_five_failures(client):
    for _ in range(5):
        assert login(client, password=WRONG).status_code == 401
    assert login(client).status_code == 429  # net teisingas slaptažodis blokuojamas


def test_rate_limit_expires(client, monkeypatch):
    for _ in range(5):
        login(client, password=WRONG)
    t = auth.now()
    monkeypatch.setattr(auth, "now", lambda: t + auth.FAILURE_WINDOW_SECONDS + 1)
    assert login(client).status_code == 200


def test_session_expires_after_idle_timeout(client, monkeypatch):
    login(client)
    t = auth.now()
    monkeypatch.setattr(auth, "now", lambda: t + auth.SESSION_IDLE_SECONDS + 1)
    assert client.get("/api/me").status_code == 401


def test_session_activity_extends_expiry(client, monkeypatch):
    login(client)
    t = auth.now()
    monkeypatch.setattr(auth, "now", lambda: t + auth.SESSION_IDLE_SECONDS - 60)
    assert client.get("/api/me").status_code == 200
    monkeypatch.setattr(auth, "now", lambda: t + 2 * auth.SESSION_IDLE_SECONDS - 120)
    assert client.get("/api/me").status_code == 200


def test_csrf_missing_returns_403(client):
    csrf = login(client).json()["csrf"]
    assert client.post("/api/change").status_code == 403
    assert client.post("/api/change", headers={"X-CSRF-Token": "blogas"}).status_code == 403
    assert client.post("/api/change", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_role_check(client):
    login(client, "vadovas")
    assert client.get("/api/secret").status_code == 403
    client.cookies.clear()
    login(client, "rivile")
    assert client.get("/api/secret").status_code == 200


def test_unauthenticated_is_401(client):
    assert client.get("/api/secret").status_code == 401


def test_logout_invalidates_session(client):
    csrf = login(client).json()["csrf"]
    token = client.cookies.get(auth.COOKIE_NAME)
    assert client.post("/api/logout").status_code == 403  # be CSRF
    assert client.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    client.cookies.set(auth.COOKIE_NAME, token)  # senas raktas nebegalioja
    assert client.get("/api/me").status_code == 401


def test_password_never_stored_plaintext(client, tmp_path):
    login(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    assert PASSWORD.encode() not in (tmp_path / "uno.db").read_bytes()
    with closing(sqlite3.connect(tmp_path / "uno.db")) as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE username='rivile'").fetchone()[0]
        tokens = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert token not in tokens  # saugomas tik žetono hash


def test_short_password_and_bad_role_rejected(client):
    with closing(auth.connect()) as conn:
        with pytest.raises(ValueError):
            auth.create_user(conn, 1, "x", "trumpas", "admin")
        with pytest.raises(ValueError):
            auth.create_user(conn, 1, "y", PASSWORD, "svecias")


def test_inactive_user_cannot_login(client):
    with closing(auth.connect()) as conn:
        conn.execute("UPDATE users SET active=0 WHERE username='rivile'")
        conn.commit()
    assert login(client).status_code == 401


# ---------- audito pataisos: vardo normalizavimas, bendras DB kelias ----------

def test_username_normalized_for_lookup_ratelimit_and_create(client):
    for variant in ("rivile ", " RIVILE", "Rivile", "rivile", " rivile "):
        assert login(client, variant, WRONG).status_code == 401
    assert login(client, "rivile  ").status_code == 429          # tas pats raktas, ne penki nauji
    with closing(auth.connect()) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            auth.create_user(conn, 1, "  RIVILE ", PASSWORD, "admin")


def test_login_is_case_and_space_insensitive(client):
    r = login(client, "  Rivile ")
    assert r.status_code == 200 and r.json()["username"] == "rivile"


def test_db_path_shared_via_env_and_cli_override(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.settings, "db_path", None)
    env_db, cli_db = tmp_path / "env.db", tmp_path / "cli.db"
    monkeypatch.setenv("UNO_DB", str(env_db))
    assert auth.db_path() == env_db
    with closing(auth.connect()):
        pass
    assert env_db.exists()
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": PASSWORD)
    code = auth.main(["create-user", "--company", "1", "--username", "Cli", "--role", "admin",
                      "--db", str(cli_db)])
    assert code == 0 and cli_db.exists()
    with closing(auth.connect(cli_db)) as conn:
        assert conn.execute("SELECT username FROM users").fetchone()[0] == "cli"
    monkeypatch.delenv("UNO_DB")
    assert auth.db_path() == auth.DEFAULT_DB


# ---------- N3: username_norm migracija ----------

def _legacy_db(path, rows):
    """DB kaip prieš username_norm: be stulpelio, vardai originaliu registru."""
    conn = sqlite3.connect(path)
    conn.executescript(auth.SCHEMA)
    for company, name in rows:
        conn.execute("INSERT INTO users(company_id, username, password_hash, role, active, created_at)"
                     " VALUES (?,?,?,?,1,0)", (company, name, auth.hash_password(PASSWORD), "accountant"))
    conn.commit()
    conn.close()


def test_username_collision_disables_accounts_and_blocks_cross_company_login(tmp_path, monkeypatch, capsys):
    db = tmp_path / "old.db"
    _legacy_db(db, [(1, "Ana"), (2, "ana"), (1, "Živilė")])
    monkeypatch.setattr(auth.settings, "db_path", db)
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        rows = {r["username"]: r for r in conn.execute("SELECT * FROM users")}
    assert rows["Ana"]["active"] == 0 and rows["ana"]["active"] == 0 and rows["Živilė"]["active"] == 1
    assert "ĮSPĖJIMAS" in capsys.readouterr().err
    app = FastAPI()
    app.include_router(auth.router)
    c = TestClient(app)
    assert login(c, "ana").status_code == 401          # nė viena įmonė neprisijungia
    assert login(c, "ANA").status_code == 401
    r = login(c, "živilė")                              # Unicode casefold veikia
    assert r.status_code == 200 and r.json()["company_id"] == 1
    assert login(c, "ŽIVILĖ").status_code == 200


def test_create_user_rejects_existing_norm(client):
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, "Živilė", PASSWORD, "accountant")
        with pytest.raises(sqlite3.IntegrityError):
            auth.create_user(conn, 2, "ŽIVILĖ", PASSWORD, "accountant")


# ---- S3 (saugumo auditas): ribota atmintis ir IP limitas ----

def test_failure_store_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED", 50)
    auth.reset_failures()
    for i in range(500):
        auth.record_failure(f"vardas{i}", f"10.0.{i // 250}.{i % 250}")
    assert len(auth._failures) <= 50 and len(auth._ip_failures) <= 50


def test_expired_failures_evicted(monkeypatch):
    auth.reset_failures()
    t = [1000.0]
    monkeypatch.setattr(auth, "now", lambda: t[0])
    monkeypatch.setattr(auth, "MAX_TRACKED", 10)
    for i in range(10):
        auth.record_failure(f"a{i}", "1.1.1.1")
    t[0] += auth.FAILURE_WINDOW_SECONDS + 1
    auth.record_failure("naujas", "2.2.2.2")
    assert len(auth._failures) == 1


def test_per_ip_limit_blocks_before_password_check(client, monkeypatch):
    auth.reset_failures()
    for i in range(auth.MAX_IP_FAILURES):
        assert login(client, f"nera{i}", WRONG).status_code == 401
    called = []
    monkeypatch.setattr(auth, "verify_password", lambda *a: called.append(1) or True)
    r = login(client, "rivile", PASSWORD)
    assert r.status_code == 429 and not called


def test_long_username_rejected_lithuanian_400():
    from app import main
    r = TestClient(main.app).post("/api/login", json={"username": "x" * 5000, "password": "a"})
    assert r.status_code == 400 and "per ilgas" in r.json()["detail"].lower()
