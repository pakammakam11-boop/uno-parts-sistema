"""VPS prisijungimo vartai: /_gate (Caddy forward_auth), UNO_DEMO_AUTH ir /healthz."""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import auth, main

PASSWORD = "labai-ilgas-slaptazodis"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.settings, "db_path", tmp_path / "uno.db")
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, "rivile", PASSWORD, "accountant")
    monkeypatch.setattr(main, "RESULTS", {})
    return TestClient(main.app, follow_redirects=False)


def login(client):
    assert client.post("/api/login", json={"username": "rivile", "password": PASSWORD}).status_code == 200


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_gate_redirects_without_session(client):
    r = client.get("/_gate")
    assert r.status_code == 302 and r.headers["location"] == "/app"


def test_gate_allows_uno_session(client):
    login(client)
    assert client.get("/_gate").status_code == 204


def test_gate_rejects_garbage_cookie(client):
    client.cookies.set(auth.COOKIE_NAME, "nesamone")
    assert client.get("/_gate").status_code == 302


def test_demo_routes_open_by_default(client):
    assert client.get("/api/overview").status_code == 200


def test_demo_routes_need_login_when_demo_auth_on(client, monkeypatch):
    monkeypatch.setenv("UNO_DEMO_AUTH", "1")
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/run/bank").status_code == 401
    assert client.get("/api/download/x.xlsx").status_code == 401
    login(client)
    assert client.get("/api/overview").status_code == 200


def test_demo_disabled_still_404_before_auth(client, monkeypatch):
    monkeypatch.setenv("UNO_DEMO", "0")
    monkeypatch.setenv("UNO_DEMO_AUTH", "1")
    assert client.get("/api/overview").status_code == 404


def test_secure_cookie_env_read_at_import():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c", "from app import auth; print(auth.settings.secure_cookie)"],
                         env={**__import__("os").environ, "UNO_SECURE_COOKIE": "1"}, capture_output=True, text=True)
    assert out.stdout.strip() == "True"


# ---- S3: personalo sesijos rolės ----
from tests.hr_testkit import (BUHALTERE, DIREKTORIUS, DOVILE, PARDAVIMU_VADOVAS, SANDELIO_VADOVE, Session,  # noqa: E402
                              hr_env)  # noqa: F401


@pytest.fixture
def hr_client(hr_env, monkeypatch, tmp_path):
    monkeypatch.setattr(auth.settings, "db_path", tmp_path / "uno.db")
    monkeypatch.setattr(main, "RESULTS", {})
    monkeypatch.setenv("UNO_DEMO_AUTH", "1")


def _hr_status(employee_id, path):
    return Session(employee_id).client.get(path, follow_redirects=False).status_code


@pytest.mark.parametrize("emp,gate_code,demo_code", [
    (DIREKTORIUS, 204, 200), (BUHALTERE, 204, 200),
    (SANDELIO_VADOVE, 204, 403), (PARDAVIMU_VADOVAS, 204, 403),
    (DOVILE, 403, 403),
])
def test_gate_and_demo_by_hr_role(hr_client, emp, gate_code, demo_code):
    assert _hr_status(emp, "/_gate") == gate_code
    assert _hr_status(emp, "/api/overview") == demo_code
    assert _hr_status(emp, "/api/download/x.xlsx") in ((404, 200) if demo_code == 200 else (403,))


def test_gate_no_session_still_redirects(hr_client):
    assert TestClient(main.app, follow_redirects=False).get("/_gate").status_code == 302


# ---- S2 (saugumo auditas): rolės, įmonės izoliacija ir CSRF apskaitos API ----

def _user_client(tmp_client, name, company, role, monkeypatch):
    with closing(auth.connect()) as conn:
        auth.create_user(conn, company, name, PASSWORD, role)
    c = TestClient(main.app, follow_redirects=False)
    r = c.post("/api/login", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200
    c.csrf = {"X-CSRF-Token": r.json()["csrf"]}
    return c


@pytest.fixture
def prod_clients(client, monkeypatch):
    monkeypatch.setenv("UNO_DEMO_AUTH", "1")
    return {
        "acc1": _user_client(client, "buh1", 1, "accountant", monkeypatch),
        "mgr1": _user_client(client, "vad1", 1, "manager", monkeypatch),
        "mgr2": _user_client(client, "vad2", 2, "manager", monkeypatch),
        "acc2": _user_client(client, "buh2", 2, "accountant", monkeypatch),
    }


SUPPLIER = {"name": "UAB Testas", "company_code": "123456789"}


def test_other_company_cannot_read_results_or_write_suppliers(prod_clients):
    for key in ("mgr2", "acc2"):
        c = prod_clients[key]
        assert c.get("/api/overview").status_code == 403
        assert c.get("/api/module/invoices").status_code == 403
        assert c.get("/api/control").status_code == 403
        assert c.post("/api/suppliers", json=SUPPLIER, headers=c.csrf).status_code == 403
        assert c.post("/api/run/bank", headers=c.csrf).status_code == 403


def test_manager_is_read_only_even_in_company_1(prod_clients):
    c = prod_clients["mgr1"]
    assert c.get("/api/overview").status_code == 200
    assert c.post("/api/suppliers", json=SUPPLIER, headers=c.csrf).status_code == 403
    assert c.post("/api/run/bank", headers=c.csrf).status_code == 403


def test_accountant_company_1_ok_and_write_needs_csrf(prod_clients):
    c = prod_clients["acc1"]
    assert c.get("/api/overview").status_code == 200
    r = c.post("/api/suppliers", json=SUPPLIER)                       # be CSRF
    assert r.status_code == 403
    assert c.post("/api/run/bank").status_code == 403
    assert c.post("/api/run/bank", headers={"X-CSRF-Token": "blogas"}).status_code == 403
    assert c.post("/api/run/bank", headers=c.csrf).status_code == 200


def test_gate_rejects_other_company_session(prod_clients):
    assert prod_clients["acc2"].get("/_gate").status_code == 302
    assert prod_clients["acc1"].get("/_gate").status_code == 204


def test_hr_director_write_needs_hr_csrf(hr_client):
    s = Session(DIREKTORIUS)
    assert s.client.post("/api/run/bank").status_code == 403
    csrf = s.client.get("/api/hr/me").json()["csrf"]
    assert s.client.post("/api/run/bank", headers={"X-CSRF-Token": csrf}).status_code == 200
