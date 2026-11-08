"""HR API per TestClient: kiekvienas žmogus savo sesija (savo cookie), kaip realioje naršyklėje."""
import io

import pdfplumber
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.main import app
from core.hr import db
from core.hr.schema import hr_employees, hr_outbox
from hr_testkit import (ANDRIUS, AUSTEJA, BUHALTERE, DIREKTORIUS, DOVILE, FEB_WEEK, JONAS, LUKAS, PARDAVIMU_VADOVAS,
                        SANDELIO_VADOVE, Session, create_leave, hr_env, next_sign_url)  # noqa: F401


def pdf_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# ---------- prisijungimas ----------

def test_me_requires_login():
    resp = TestClient(app).get("/api/hr/me")
    assert resp.status_code == 401 and resp.json() == {"detail": "Reikia prisijungti"}


def test_demo_login_me_and_cookie_flags(hr_env):
    client = TestClient(app)
    resp = client.post("/api/hr/demo/login", json={"employee_id": DOVILE})
    cookie = resp.headers["set-cookie"].lower()
    assert "uno_hr_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie
    me = client.get("/api/hr/me").json()
    assert me["employee"]["name"] == "Dovilė Paulauskaitė" and me["roles"] == ["employee"]
    assert me["balance"] == 7.0 and me["csrf"] and me["demo"] is True


def test_secure_cookie_when_enabled(hr_env, monkeypatch):
    monkeypatch.setenv("HR_SECURE_COOKIE", "1")
    resp = TestClient(app).post("/api/hr/demo/login", json={"employee_id": JONAS})
    assert "secure" in resp.headers["set-cookie"].lower()


def test_secure_cookie_automatic_on_vercel(hr_env, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    resp = TestClient(app).post("/api/hr/demo/login", json={"employee_id": JONAS})
    assert "secure" in resp.headers["set-cookie"].lower()


def test_session_cookie_is_refreshed_on_authenticated_responses(hr_env):
    s = Session(DOVILE)
    resp = s.get("/me")
    cookie = resp.headers["set-cookie"].lower()
    assert "uno_hr_session=" in cookie and "max-age=28800" in cookie and "httponly" in cookie
    assert "set-cookie" not in TestClient(app).get("/api/hr/me").headers     # 401 be slapuko
    assert "set-cookie" not in s.get("/requests/999").headers                # 404 be slapuko


def test_validation_errors_are_400_lithuanian_not_422(hr_env):
    s = Session(DOVILE)
    resp = s.client.post("/api/hr/requests", json=["blogas"], headers={"X-CSRF-Token": s.csrf})
    assert resp.status_code == 400 and "Neteisingi duomenys" in resp.json()["detail"]
    resp = s.get("/requests/abc")
    assert resp.status_code == 400 and "Neteisingi duomenys" in resp.json()["detail"]


def test_csrf_required_on_post(hr_env):
    s = Session(DOVILE)
    assert s.post("/workdays", FEB_WEEK, csrf=False).status_code == 403
    assert s.client.post("/api/hr/workdays", json=FEB_WEEK, headers={"X-CSRF-Token": "blogas"}).status_code == 403
    assert s.post("/workdays", FEB_WEEK).status_code == 200
    assert s.post("/requests", {}, csrf=False).status_code == 403
    assert s.post("/logout", csrf=False).status_code == 403


def test_logout_ends_session(hr_env):
    s = Session(DOVILE)
    assert s.post("/logout").json() == {"ok": True}
    assert s.get("/me").status_code == 401


def test_demo_endpoints_disabled_without_hr_demo(hr_env, monkeypatch):
    monkeypatch.delenv("HR_DEMO")
    client = TestClient(app)
    assert client.get("/api/hr/demo/personas").status_code == 404
    assert client.post("/api/hr/demo/login", json={"employee_id": JONAS}).status_code == 404
    assert client.post("/api/hr/demo/reset", json={}).status_code == 404


def test_personas_list(hr_env):
    personas = TestClient(app).get("/api/hr/demo/personas").json()
    assert len(personas) == 12
    by_name = {p["name"]: p for p in personas}
    assert by_name["Jūratė Baltrūnienė"]["roles"] == ["employee", "hr"]
    assert "manager" in by_name["Tomas Vaitkus"]["roles"]


def test_magic_link_login_flow(hr_env):
    client = TestClient(app)
    assert client.post("/api/hr/login/request", json={"email": "ieva@demo.uno.lt"}).json() == {"ok": True}
    assert client.post("/api/hr/login/request", json={"email": "nera@demo.uno.lt"}).json() == {"ok": True}
    with db.tx() as conn:
        link = conn.execute(select(hr_outbox.c.link).where(hr_outbox.c.kind == "login_link")).scalar_one()
    assert link.startswith("/hr/login/")
    resp = client.get(link, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/hr"
    assert client.get("/api/hr/me").json()["employee"]["name"] == "Ieva Butkutė"
    again = TestClient(app).get(link, follow_redirects=False)
    assert again.headers["location"] == "/hr?link=invalid" and "set-cookie" not in again.headers


def test_hr_page_served(hr_env):
    resp = TestClient(app).get("/hr")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"
    assert TestClient(app).get("/hr/sign/abc/s1").status_code == 200


# ---------- pagalbiniai ----------

def test_colleagues_exclude_own_manager(hr_env):
    ids = {p["id"] for p in Session(DOVILE).get("/colleagues").json()}
    assert PARDAVIMU_VADOVAS not in ids and DOVILE not in ids and ANDRIUS in ids
    director_ids = {p["id"] for p in Session(DIREKTORIUS).get("/colleagues").json()}
    assert DIREKTORIUS not in director_ids and len(director_ids) == 11


def test_colleagues_exclude_self(hr_env):
    people = Session(DOVILE).get("/colleagues").json()
    assert len(people) == 10 and DOVILE not in [p["id"] for p in people]   # be savęs ir be vadovo


def test_workdays_endpoint(hr_env):
    s = Session(DOVILE)
    body = s.post("/workdays", {"date_from": "2027-02-15", "date_to": "2027-02-19"}).json()
    assert body["work_days"] == 4 and body["holidays"][0]["date"] == "2027-02-16"
    bad = s.post("/workdays", {"date_from": "2027-02-19", "date_to": "2027-02-15"})
    assert bad.status_code == 400 and "detail" in bad.json()


# ---------- atostogų srautas (3 sesijos) ----------

def test_full_leave_flow_three_sessions(hr_env):
    employee, substitute, manager, hr = Session(DOVILE), Session(ANDRIUS), Session(PARDAVIMU_VADOVAS), Session(BUHALTERE)
    resp = create_leave(employee, ANDRIUS, comment="Šeimos šventė")
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert req["status"] == "pending_signatures" and req["work_days"] == 5 and req["warnings"] == []
    assert req["my_turn"] and req["sign_url"].startswith("/hr/sign/") and req["can_cancel"]
    rid = req["id"]
    assert [s["role"] for s in req["signers"]] == ["employee", "substitute", "manager"]

    assert substitute.get("/requests?scope=to_sign").json() == []       # dar ne jo eilė
    assert employee.get("/requests?scope=to_sign").json()[0]["id"] == rid

    info = employee.get("/sign/" + req["sign_url"].split("/sign/")[1]).json()
    assert info["can_sign"] and len(info["control_code"]) == 4 and info["document"]["sha256"]
    assert employee.sign(req["sign_url"]).status_code == 200

    early = manager.get(f"/requests/{rid}").json()
    assert early["sign_url"] is None and early["my_turn"] is False       # vadovas mato, bet ne jo eilė
    assert manager.sign(early["my_sign_url"]).status_code == 409

    assert [r["id"] for r in substitute.get("/requests?scope=to_sign").json()] == [rid]
    wrong = substitute.sign(next_sign_url(manager, rid))                 # svetimas parašas
    assert wrong.status_code == 403
    assert substitute.sign(next_sign_url(substitute, rid)).status_code == 200
    final = manager.sign(next_sign_url(manager, rid))
    assert final.status_code == 200 and final.json()["status"] == "approved"

    assert employee.balance() == 2.0
    assert [m["kind"] for m in employee.get("/outbox").json()] == ["approved"]
    sheet = hr.get("/timesheet?month=2027-02").json()
    row = next(e for e in sheet["employees"] if e["id"] == DOVILE)
    assert sorted(row["marks"]) == ["2027-02-01", "2027-02-02", "2027-02-03", "2027-02-04", "2027-02-05"]
    assert set(row["marks"].values()) == {"A"}
    assert len(hr.get("/outbox").json()) >= 3 and len(manager.get("/outbox").json()) == 1

    detail = employee.get(f"/requests/{rid}").json()
    actions = [a["action"] for a in detail["audit"]]
    assert actions[:2] == ["created", "document_frozen"] and "approved" in actions and "balance_decreased" in actions
    assert detail["can_cancel"] is False


def test_documents_original_and_signed_pdf(hr_env):
    employee, substitute, manager = Session(DOVILE), Session(ANDRIUS), Session(PARDAVIMU_VADOVAS)
    req = create_leave(employee, ANDRIUS).json()
    doc = req["documents"][0]
    assert employee.get(f"/documents/{doc['id']}.pdf?signed=1").status_code == 404   # dar nepasirašytas
    original = employee.get(f"/documents/{doc['id']}.pdf")
    assert original.status_code == 200 and original.content.startswith(b"%PDF")
    assert "attachment" in original.headers["content-disposition"]
    for s in (employee, substitute, manager):
        assert s.sign(next_sign_url(s, req["id"])).status_code == 200
    signed = employee.get(f"/documents/{doc['id']}.pdf?signed=1")
    text = pdf_text(signed.content)
    assert "Demo parašas (imitacija)" in text and "Pavaduojantis" in text and doc["sha256"][:32] in text
    assert "Prašymas" in pdf_text(original.content)


def test_decline_flow(hr_env):
    employee, substitute = Session(DOVILE), Session(ANDRIUS)
    req = create_leave(employee, ANDRIUS).json()
    employee.sign(req["sign_url"])
    url = next_sign_url(substitute, req["id"])
    assert substitute.sign(url, "decline", None).status_code == 400
    resp = substitute.sign(url, "decline", "Tuo metu atostogauju")
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "rejected" and body["reason"] == "Tuo metu atostogauju"
    assert body["declined_by"]["id"] == ANDRIUS and body["can_cancel"] is False
    assert employee.balance() == 7.0
    assert employee.post(f"/requests/{req['id']}/cancel").status_code == 409
    assert [m["kind"] for m in employee.get("/outbox").json()] == ["declined"]


def test_cancel_flow(hr_env):
    employee, substitute = Session(DOVILE), Session(ANDRIUS)
    req = create_leave(employee, ANDRIUS).json()
    employee.sign(req["sign_url"])
    assert substitute.post(f"/requests/{req['id']}/cancel").status_code == 403     # ne autorius
    resp = employee.post(f"/requests/{req['id']}/cancel")
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"


def test_signing_after_cancel_is_rejected(hr_env):
    employee, substitute = Session(DOVILE), Session(ANDRIUS)
    req = create_leave(employee, ANDRIUS).json()
    employee.sign(req["sign_url"])
    sub_url = next_sign_url(substitute, req["id"])
    employee.post(f"/requests/{req['id']}/cancel")
    assert substitute.sign(sub_url).status_code == 409


def test_validation_errors_are_lithuanian_details(hr_env):
    s = Session(JONAS)
    resp = create_leave(s, LUKAS)
    assert resp.status_code == 400 and resp.json()["detail"] == "Nepakanka likučio: liko 3,5 d."
    assert create_leave(s, JONAS, leave_kind="unpaid").status_code == 400
    assert create_leave(s, LUKAS, leave_kind="unpaid").status_code == 200
    assert create_leave(s, LUKAS, leave_kind="unpaid", date_from="2027-02-02", date_to="2027-02-03").status_code == 409
    assert s.post("/requests", {"type": "xx"}).status_code == 400


def test_past_request_returns_warning(hr_env):
    resp = create_leave(Session(JONAS), LUKAS, leave_kind="unpaid", date_from="2026-01-05", date_to="2026-01-05")
    assert resp.status_code == 200 and resp.json()["warnings"]


# ---------- komandiruotė ----------

def test_trip_flow_with_order(hr_env):
    employee, substitute, manager, hr = Session(JONAS), Session(LUKAS), Session(SANDELIO_VADOVE), Session(BUHALTERE)
    body = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Ryga", "purpose": "Tiekėjo vizitas"}
    req = employee.post("/requests", body).json()
    rid = req["id"]
    for s in (employee, substitute, manager):
        assert s.sign(next_sign_url(s, rid)).status_code == 200
    after = employee.get(f"/requests/{rid}").json()
    assert after["status"] == "approved" and after["order_status"] == "pending_order"
    assert [d["kind"] for d in after["documents"]] == ["request", "order"]
    assert hr.get("/timesheet?month=2027-02").json()["employees"][0]["marks"] == {}
    assert employee.balance() == 3.5

    to_sign = manager.get("/requests?scope=to_sign").json()
    assert [r["id"] for r in to_sign] == [rid]
    done = manager.sign(next_sign_url(manager, rid)).json()
    assert done["order_status"] == "order_signed"
    sheet = hr.get("/timesheet?month=2027-02").json()
    marks = next(e for e in sheet["employees"] if e["id"] == JONAS)["marks"]
    assert len(marks) == 5 and set(marks.values()) == {"K"}
    order_doc = next(d for d in done["documents"] if d["kind"] == "order")
    assert "Įsakymas" in pdf_text(manager.client.get(order_doc["signed_url"]).content)
    assert employee.balance() == 3.5


def test_director_trip_order_signed_by_himself(hr_env):
    director, substitute = Session(DIREKTORIUS), Session(SANDELIO_VADOVE)
    body = {"type": "trip", "substitute_id": SANDELIO_VADOVE, **FEB_WEEK, "destination": "Vilnius", "purpose": "Mugė"}
    rid = director.post("/requests", body).json()["id"]
    director.sign(next_sign_url(director, rid))
    substitute.sign(next_sign_url(substitute, rid))
    assert director.get(f"/requests/{rid}").json()["order_status"] is None   # dar laukia direktoriaus parašo kaip vadovo
    director.sign(next_sign_url(director, rid))
    assert director.get(f"/requests/{rid}").json()["order_status"] == "pending_order"
    assert director.sign(next_sign_url(director, rid)).json()["order_status"] == "order_signed"


# ---------- prieiga ----------

def test_foreign_request_and_documents_are_404(hr_env):
    employee = Session(DOVILE)
    req = create_leave(employee, ANDRIUS).json()
    doc_id = req["documents"][0]["id"]
    for stranger in (Session(AUSTEJA), Session(SANDELIO_VADOVE), Session(JONAS)):
        assert stranger.get(f"/requests/{req['id']}").status_code == 404
        assert stranger.post(f"/requests/{req['id']}/cancel").status_code == 404
        assert stranger.get(f"/documents/{doc_id}.pdf").status_code == 404
        assert stranger.get("/requests?scope=mine").json() == []
    token_path = req["sign_url"].split("/sign/")[1]
    assert Session(AUSTEJA).get(f"/sign/{token_path}").status_code == 404
    assert Session(AUSTEJA).post(f"/sign/{token_path}", {"action": "sign"}).status_code == 404


def test_scopes_and_permissions(hr_env):
    req = create_leave(Session(DOVILE), ANDRIUS).json()
    manager, hr, plain = Session(PARDAVIMU_VADOVAS), Session(BUHALTERE), Session(AUSTEJA)
    assert [r["id"] for r in manager.get("/requests?scope=team").json()] == [req["id"]]
    assert Session(SANDELIO_VADOVE).get("/requests?scope=team").json() == []
    assert plain.get("/requests?scope=team").status_code == 403
    assert plain.get("/requests?scope=all").status_code == 403
    assert manager.get("/requests?scope=all").status_code == 403
    assert [r["id"] for r in hr.get("/requests?scope=all").json()] == [req["id"]]
    assert hr.get(f"/requests/{req['id']}").status_code == 200
    assert hr.get("/requests?scope=nera").status_code == 400


def test_timesheet_permissions(hr_env):
    assert Session(JONAS).get("/timesheet").status_code == 403
    assert Session(BUHALTERE).get("/timesheet?month=bla").status_code == 400
    hr_rows = Session(BUHALTERE).get("/timesheet?month=2027-02").json()["employees"]
    dept = Session(PARDAVIMU_VADOVAS).get("/timesheet?month=2027-02").json()["employees"]
    assert len(hr_rows) == 12 and {e["department"] for e in dept} == {"Pardavimai"} and len(dept) == 4
    days = Session(BUHALTERE).get("/timesheet?month=2027-02").json()["days"]
    assert len(days) == 28 and next(d for d in days if d["date"] == "2027-02-16")["holiday"]


def test_timesheet_manager_sees_recursive_subordinates_director_sees_all(hr_env):
    assert len(Session(DIREKTORIUS).get("/timesheet?month=2027-02").json()["employees"]) == 12

    def ids(s):
        return {e["id"] for e in s.get("/timesheet?month=2027-02").json()["employees"]}

    assert ids(Session(SANDELIO_VADOVE)) == {SANDELIO_VADOVE, JONAS, LUKAS, 7}
    with db.tx() as conn:   # Jonas tampa Andriaus vadovu: Rasa (Jono vadovė) mato ir Andrių
        conn.execute(update(hr_employees).where(hr_employees.c.id == ANDRIUS).values(manager_id=JONAS))
    assert ANDRIUS in ids(Session(SANDELIO_VADOVE)) and ANDRIUS in ids(Session(JONAS))
    assert ANDRIUS not in ids(Session(PARDAVIMU_VADOVAS))


def test_destination_over_200_chars_is_lithuanian_400(hr_env):
    body = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "x" * 201, "purpose": "Mugė"}
    resp = Session(JONAS).post("/requests", body)
    assert resp.status_code == 400 and "200" in resp.json()["detail"]


def test_outbox_visibility(hr_env):
    req = create_leave(Session(DOVILE), ANDRIUS).json()
    Session(DOVILE).sign(req["sign_url"])
    assert len(Session(ANDRIUS).get("/outbox").json()) == 1
    assert Session(AUSTEJA).get("/outbox").json() == []
    assert len(Session(BUHALTERE).get("/outbox").json()) == 1


# ---------- postback per HTTP, demo reset ----------

def test_postback_http_needs_secret(hr_env, monkeypatch):
    employee = Session(DOVILE)
    req = create_leave(employee, ANDRIUS).json()
    token, signer_id = req["sign_url"].split("/")[3:5]
    body = {"token": token, "signer_id": signer_id, "event": "signer_signed"}
    anon = TestClient(app)
    assert anon.post("/api/hr/postback", json=body).status_code == 403          # paslaptis nenustatyta
    monkeypatch.setenv("HR_POSTBACK_SECRET", "paslaptis")
    assert anon.post("/api/hr/postback", json=body, headers={"X-Postback-Secret": "bloga"}).status_code == 403
    ok = anon.post("/api/hr/postback", json=body, headers={"X-Postback-Secret": "paslaptis"})
    assert ok.status_code == 200 and ok.json()["changed"] is True
    again = anon.post("/api/hr/postback", json=body, headers={"X-Postback-Secret": "paslaptis"})
    assert again.json()["changed"] is False


def test_demo_reset_restores_clean_data(hr_env):
    employee, hr = Session(DOVILE), Session(BUHALTERE)
    req = create_leave(employee, ANDRIUS).json()
    for s in (employee, Session(ANDRIUS), Session(PARDAVIMU_VADOVAS)):
        s.sign(next_sign_url(s, req["id"]))
    assert employee.balance() == 2.0
    assert employee.post("/demo/reset").status_code == 403           # ne HR
    assert hr.post("/demo/reset").json() == {"ok": True}
    assert employee.balance() == 7.0 and employee.get("/requests").json() == []
    assert hr.get("/requests?scope=all").json() == [] and hr.get("/outbox").json() == []
    assert employee.get("/me").status_code == 200                    # sesijos išlieka
    assert create_leave(employee, ANDRIUS).status_code == 200


# ---- S1 (saugumo auditas): HR negali perimti svetimų prisijungimo nuorodų ----

def test_hr_cannot_read_directors_login_link_via_outbox(hr_env):
    from core.hr import login as hr_login
    with db.tx() as conn:
        director = conn.execute(select(hr_employees.c.email).where(hr_employees.c.id == DIREKTORIUS)).scalar_one()
        hr_login.request_magic_link(conn, director, "1.2.3.4")
    hr_mail = Session(BUHALTERE).get("/outbox").json()
    login_rows = [m for m in hr_mail if m["kind"] == "login_link"]
    assert login_rows, "HR mato laišką, bet be nuorodos"
    for m in login_rows:
        assert m["link"] is None and "/hr/login/" not in (m["body"] or "")
    assert "/hr/login/" not in str(hr_mail)
    own = Session(DIREKTORIUS).get("/outbox").json()          # demo: gavėjas mato savo nuorodą
    assert any((m["link"] or "").startswith("/hr/login/") for m in own)


def test_prod_login_link_not_stored_in_outbox(hr_env, monkeypatch):
    from core.hr import login as hr_login
    monkeypatch.setenv("HR_DEMO", "0")
    with db.tx() as conn:
        director = conn.execute(select(hr_employees.c.email).where(hr_employees.c.id == DIREKTORIUS)).scalar_one()
        hr_login.request_magic_link(conn, director, "1.2.3.4")
    with db.tx() as conn:
        rows = conn.execute(select(hr_outbox.c.link).where(hr_outbox.c.kind == "login_link")).scalars().all()
    assert rows and all(link is None for link in rows)
