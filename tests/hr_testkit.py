"""Bendri HR testų pagalbininkai: laikina SQLite, demo aplinka, API sesijos (po vieną klientą kiekvienam žmogui)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core.hr import db, login, people, seed

# Demo darbuotojų ID (core/hr/seed.py)
DIREKTORIUS, SANDELIO_VADOVE, PARDAVIMU_VADOVAS = 1, 2, 3
JONAS, LUKAS = 5, 6                  # Sandėlis, vadovė Rasa (2)
DOVILE, ANDRIUS, AUSTEJA = 8, 9, 10  # Pardavimai, vadovas Mantas (3)
BUHALTERE = 11

FEB_WEEK = {"date_from": "2027-02-01", "date_to": "2027-02-05"}  # pirm.-penkt., 5 darbo d.


@pytest.fixture
def hr_env(monkeypatch, tmp_path):
    for var in ("DATABASE_URL", "RESEND_API_KEY", "HR_MAIL_ALLOWLIST", "HR_BASE_URL", "HR_POSTBACK_SECRET", "VERCEL",
                "HR_SECURE_COOKIE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HR_DATABASE_URL", f"sqlite:///{tmp_path / 'hr_test.db'}")
    monkeypatch.setenv("HR_DEMO", "1")
    monkeypatch.setenv("HR_SIGNING", "demo")
    login.reset_rate_limits()
    seed.ready()
    return tmp_path


def actor_for(employee_id: int) -> people.Actor:
    with db.tx() as conn:
        return people.make_actor(conn, people.get_employee(conn, employee_id), "csrf-test")


class Session:
    """Vieno žmogaus naršyklė: savo cookie ir CSRF."""

    def __init__(self, employee_id: int):
        self.client = TestClient(app)
        resp = self.client.post("/api/hr/demo/login", json={"employee_id": employee_id})
        assert resp.status_code == 200, resp.text
        self.csrf = resp.json()["csrf"]
        self.id = employee_id

    def get(self, path: str, **kw):
        return self.client.get(f"/api/hr{path}", **kw)

    def post(self, path: str, json: dict | None = None, csrf: bool = True):
        headers = {"X-CSRF-Token": self.csrf} if csrf else {}
        return self.client.post(f"/api/hr{path}", json=json if json is not None else {}, headers=headers)

    def balance(self) -> float:
        return self.get("/me").json()["balance"]

    def sign(self, sign_url: str, action: str = "sign", reason: str | None = None):
        _, _, _, token, signer_id = sign_url.split("/")  # /hr/sign/{token}/{signer_id}
        return self.post(f"/sign/{token}/{signer_id}", {"action": action, "reason": reason})


def create_leave(session: Session, substitute_id: int, **overrides):
    body = {"type": "leave", "leave_kind": "annual", "substitute_id": substitute_id, **FEB_WEEK, **overrides}
    return session.post("/requests", body)


def next_sign_url(session: Session, request_id: int) -> str:
    data = session.get(f"/requests/{request_id}").json()
    url = data["sign_url"] or data["my_sign_url"]
    assert url, "nėra pasirašymo nuorodos šiam naudotojui"
    return url
