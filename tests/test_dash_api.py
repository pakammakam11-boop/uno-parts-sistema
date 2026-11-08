"""Dashboard API per TestClient su HR sesijomis: teisės, formos, filtrai, išskleidimas, puslapiai."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from dash_testkit import freeze
from hr_testkit import (BUHALTERE, DIREKTORIUS, DOVILE, FEB_WEEK, JONAS, PARDAVIMU_VADOVAS, SANDELIO_VADOVE, Session,
                        create_leave, hr_env, next_sign_url)  # noqa: F401

ENDPOINTS = ["/summary", "/costs", "/costs/transport", "/costs/transport/circle-k-lietuva", "/sales/series",
             "/efficiency", "/work", "/operations", "/sources"]


@pytest.fixture
def env(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    return hr_env


def get(session: Session, path: str, **params):
    return session.client.get(f"/api/dash{path}", params=params)


# ---------- teisės ----------

@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_session_is_401(env, path):
    resp = TestClient(app).get(f"/api/dash{path}")
    assert resp.status_code == 401 and resp.json() == {"detail": "Reikia prisijungti"}


@pytest.mark.parametrize("who", [DOVILE, PARDAVIMU_VADOVAS, SANDELIO_VADOVE])
def test_employees_and_managers_are_403(env, who):
    resp = get(Session(who), "/summary")
    assert resp.status_code == 403 and resp.json() == {"detail": "Apžvalgą mato tik direktorius ir buhalterė."}


@pytest.mark.parametrize("who", [DIREKTORIUS, BUHALTERE])
def test_director_and_hr_see_everything(env, who):
    session = Session(who)
    for path in ENDPOINTS:
        resp = get(session, path)
        assert resp.status_code == 200, (path, resp.text)
        body = resp.json()
        assert body["source"] and body["as_of"] and body["status"] in ("demo", "connected")


def test_bad_parameters_are_400_in_lithuanian(env):
    s = Session(DIREKTORIUS)
    for path in ("/summary", "/costs", "/sales/series", "/efficiency", "/operations", "/costs/transport"):
        bad_period = get(s, path, period="year")
        assert bad_period.status_code == 400 and bad_period.json() == {"detail": "Neteisingas laikotarpis (day, week, month, quarter)."}
        bad_branch = get(s, path, branch="riga")
        assert bad_branch.status_code == 400 and "filialas" in bad_branch.json()["detail"]


def test_unknown_group_or_supplier_is_404(env):
    s = Session(DIREKTORIUS)
    assert get(s, "/costs/nera").status_code == 404
    resp = get(s, "/costs/transport/nera-tokio")
    assert resp.status_code == 404 and resp.json() == {"detail": "Tiekėjas nerastas šiame laikotarpyje"}


# ---------- formos ----------

def kpis_by_key(body: dict) -> dict:
    return {k["key"]: k for k in body["kpis"]}


def test_summary_shape_matches_ui_contract(env):
    body = get(Session(DIREKTORIUS), "/summary", period="month", branch="all").json()
    assert set(body) >= {"period", "branch", "range", "kpis", "forecast", "budget_by_branch", "actions", "source",
                         "as_of", "status"}
    assert body["range"]["label"]
    kpis = kpis_by_key(body)
    assert list(kpis) == ["sales", "gross_profit", "costs", "net"]
    for k in kpis.values():
        assert set(k) >= {"key", "label", "value", "prev", "delta_pct", "source", "as_of", "status"}
    assert "margin_pct" in kpis["gross_profit"]
    assert set(body["forecast"]) >= {"forecast", "budget", "fact_to_date", "pct_of_budget", "remaining_workdays", "method"}
    assert len(body["actions"]) >= 3
    for a in body["actions"]:
        assert set(a) >= {"id", "severity", "title", "reason", "link", "source"}
        assert a["severity"] in ("high", "medium", "low")
        assert a["link"] in ("#kastai/transport", "#operacijos", "#darbai", "#finansai", "/hr")
    assert body["status"] == "demo" and body["status_label"] == "Demo duomenys" and body["as_of"] == "2026-11-06"


def test_changing_period_and_branch_changes_numbers(env):
    s = Session(DIREKTORIUS)

    def sales(**kw):
        return kpis_by_key(get(s, "/summary", **kw).json())["sales"]["value"]

    month, quarter, day = sales(period="month"), sales(period="quarter"), sales(period="day")
    assert quarter > month > day > 0
    assert get(s, "/summary", period="day").json()["range"]["from"] == "2026-11-06"
    assert 0 < sales(period="month", branch="vln") < month


def test_costs_endpoints_match_ui_contract(env):
    s = Session(BUHALTERE)
    top = get(s, "/costs", period="month").json()
    assert {g["key"] for g in top["groups"]} == {"du", "rent", "transport", "utilities", "it", "ic_fees", "other"}
    assert set(top["groups"][0]) >= {"key", "label", "amount", "prev", "delta_pct", "source"}
    assert top["total"] > 0 and "prev_total" in top
    assert next(g for g in top["groups"] if g["key"] == "transport")["source"] == "Rivilė + Bankas"
    sup = get(s, "/costs/transport", period="month").json()
    assert sup["group"]["label"] == "Transportas ir kuras"
    assert set(sup["suppliers"][0]) >= {"name", "amount", "docs_count"}
    docs = get(s, "/costs/transport/" + sup["suppliers"][0]["name"], period="month").json()   # veikia ir su pavadinimu
    assert set(docs["documents"][0]) >= {"date", "number", "description", "amount"}
    assert round(sum(d["amount"] for d in docs["documents"]), 2) == pytest.approx(docs["total"], abs=0.02)


def test_sales_series_follows_period_and_continues_into_forecast(env):
    s = Session(DIREKTORIUS)
    body = get(s, "/sales/series", period="week").json()
    assert set(body["points"][0]) >= {"date", "sales", "gross_profit", "kind"}
    facts = [p for p in body["points"] if p["kind"] == "fact"]
    forecast = [p for p in body["points"] if p["kind"] == "forecast"]
    assert facts[0]["date"] == "2026-11-02" and facts[-1]["date"] == "2026-11-06"      # savaitė nuo pirmadienio, ne visas mėnuo
    quarter = get(s, "/sales/series", period="quarter").json()
    assert quarter["range"] == {"from": "2026-10-01", "to": "2026-11-06"} and quarter["points"][0]["date"] == "2026-10-01"
    day = get(s, "/sales/series", period="day").json()
    assert [p["date"] for p in day["points"] if p["kind"] == "fact"] == ["2026-11-06"]
    assert forecast[-1]["date"] == "2026-11-30" and all(p["sales"] > 0 for p in forecast)
    assert body["forecast_month"]["month"] == "2026-11" and "text" in body["how"]


def test_series_without_forecast_when_range_does_not_reach_yesterday(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 9))          # pirmadienis: vakar sekmadienis, "diena" = šeštadienis 11-07
    body = get(Session(DIREKTORIUS), "/sales/series", period="day").json()
    assert [p["date"] for p in body["points"]] == ["2026-11-07"]
    assert body["forecast_month"] is None and body["how"] is None


def test_range_labels_have_no_dashes(env):
    s = Session(DIREKTORIUS)
    for period in ("day", "week", "month", "quarter"):
        body = get(s, "/summary", period=period).json()
        for rng in (body["range"], body["previous_range"]):
            assert "\u2013" not in rng["label"] and "\u2014" not in rng["label"], rng
    assert get(s, "/summary", period="month").json()["range"]["label"] == "Mėnuo nuo 2026-11-01 iki 2026-11-06"


def test_efficiency_matches_ui_contract(env):
    eff = get(Session(DIREKTORIUS), "/efficiency").json()
    assert set(eff) >= {"fte", "hours", "lines", "per_fte", "profit_per_line", "profit_per_hour", "manufacturers",
                        "employees", "branches"}
    assert eff["fte"] == 54.5 and abs(eff["per_fte"] - eff["kpis"]["gross_profit"] / 54.5) < 0.01
    assert set(eff["manufacturers"][0]) >= {"name", "sales", "profit", "margin_pct"} and len(eff["manufacturers"]) == 9
    assert set(eff["employees"][0]) >= {"name", "branch", "sales", "profit", "hours", "profit_per_hour"}
    assert abs(sum(e["sales"] for e in eff["employees"]) - eff["kpis"]["revenue"]) < 0.5
    assert {b["key"] for b in eff["branches"]} == {"vln", "kns"}
    assert set(eff["branches"][0]) >= {"key", "label", "sales", "profit", "margin_pct", "fte", "budget_sales", "budget_profit"}


def test_work_and_operations_match_ui_contract(env):
    s = Session(DIREKTORIUS)
    work = get(s, "/work").json()
    assert set(work["emails"][0]) >= {"from", "subject", "priority", "waiting_hours", "mailbox"}
    assert set(work["tasks"][0]) >= {"title", "due", "done"} and any(t["overdue"] for t in work["tasks"])
    high = [m["waiting_hours"] for m in work["emails"] if m["priority"] == "high"]
    assert high == sorted(high, reverse=True)
    ops = get(s, "/operations", period="day").json()
    assert set(ops["calls"]) >= {"missed", "missed_yesterday", "conversion", "avg_order", "lost_revenue"}
    assert ops["calls"]["missed_yesterday"] == 23 and ops["calls"]["conversion"] == 35
    assert set(ops["deposits"]) >= {"count", "value", "overdue_count", "overdue_value", "overdue_days"}
    assert set(ops["returns"]) >= {"pct", "count", "value"} and 8 < ops["returns"]["pct"] < 12
    assert set(ops["routes"]) >= {"stops", "km", "late", "on_time_pct"}
    assert set(ops["hr"]) >= {"pending_signatures", "on_vacation"} and ops["hr"]["link"] == "/hr"
    assert ops["accounting"]["held_invoices"] == 3 and ops["accounting"]["close_errors"] == 2
    for part in ("calls", "deposits", "returns", "routes", "hr", "accounting"):
        assert ops[part]["source"] and ops[part]["as_of"] and ops[part]["status"] in ("demo", "connected")


def test_access_endpoint(env):
    assert TestClient(app).get("/api/dash/access").status_code == 401
    assert get(Session(DIREKTORIUS), "/access").json()["allowed"] is True
    assert get(Session(BUHALTERE), "/access").json()["allowed"] is True
    employee = get(Session(DOVILE), "/access")
    assert employee.status_code == 200 and employee.json()["allowed"] is False


def test_sources_texts_and_statuses(env):
    body = get(Session(DIREKTORIUS), "/sources").json()
    by_id = {s["id"]: s for s in body["sources"]}
    assert list(by_id) == ["rivile", "intercars", "bankas", "telefonija", "pastas", "personalas", "apskaita"]
    assert by_id["rivile"]["real"]["label"] == "Laukia: read-only SQL view"
    assert "icapi@intercars.eu" in by_id["intercars"]["needs"] and by_id["intercars"]["real"]["status"] == "waiting"
    assert by_id["personalas"]["status"] == "connected" and by_id["rivile"]["status"] == "demo"
    assert all(s["status_label"] and s["needs"] and s["from"] and s["covers"] and s["todo"] and s["contact"] for s in body["sources"])


def test_operations_shows_hr_leave_this_week_and_pending(env, monkeypatch):
    freeze(monkeypatch, date(2027, 1, 27))
    dovile = Session(DOVILE)
    rid = create_leave(dovile, JONAS).json()["id"]
    dovile.sign(next_sign_url(dovile, rid))
    jonas = Session(JONAS)
    jonas.sign(next_sign_url(jonas, rid))
    manager = Session(PARDAVIMU_VADOVAS)
    manager.sign(next_sign_url(manager, rid))
    assert dovile.get(f"/requests/{rid}").json()["status"] == "approved"
    freeze(monkeypatch, date(2027, 2, 3))                       # ta pati savaitė kaip FEB_WEEK (2027-02-01..05)
    ops = get(Session(DIREKTORIUS), "/operations").json()
    assert ops["hr"]["week"] == {"from": "2027-02-01", "to": "2027-02-07"}
    assert [p["name"] for p in ops["hr"]["on_leave_this_week"]] == ["Dovilė Paulauskaitė"]


# ---------- puslapiai ----------

def test_root_is_dashboard_and_apskaita_is_old_demo(env):
    client = TestClient(app)
    root = client.get("/")
    assert root.status_code == 200 and root.headers["cache-control"] == "no-cache"
    assert "text/html" in root.headers["content-type"]
    old = client.get("/apskaita")
    assert old.status_code == 200 and old.headers["cache-control"] == "no-cache"
    assert 'id="ledger"' in old.text or "Apskaita" in old.text or "UNO" in old.text
    assert client.get("/api/overview").status_code == 200       # senieji apskaitos maršrutai nepaliesti
