"""Personalo prašymų UI (/hr) srautai naršyklėje. Paleidimas: py -3.12 -m pytest -q tests/e2e/test_hr.py

Vienas uvicorn serveris moduliui (HR_DEMO=1, laikina SQLite per HR_DATABASE_URL); prieš kiekvieną testą
POST /api/hr/demo/reset. Žmonės perjungiami per „Prisijungti kaip…“ (demo personos). Jokių sleep: tik expect(...).
HR_E2E_BASE_URL=http://... leidžia tuos pačius testus prieš jau veikiantį serverį (pvz. Vercel demo).
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time
from datetime import date, timedelta

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, expect

SIGN_TIMEOUT = 15_000  # demo Smart-ID laukia ~2 s, paskui grįžta į prašymą
EXTERNAL = os.environ.get("HR_E2E_BASE_URL", "").rstrip("/")
ENV = {"HR_DEMO": "1", "RESEND_API_KEY": None, "HR_MAIL_ALLOWLIST": None, "HR_BASE_URL": None, "DATABASE_URL": None}


# ---------- serveris ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> str:
    if EXTERNAL:
        yield EXTERNAL
        return
    db = tmp_path_factory.mktemp("hr_e2e") / "hr.db"
    wanted = {**ENV, "HR_DATABASE_URL": f"sqlite:///{db.as_posix()}"}
    saved = {k: os.environ.get(k) for k in wanted}
    for k, v in wanted.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    from app import main  # importuojama po env nustatymo

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "uvicorn nepasileido"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def fresh_demo(server: str) -> None:
    """Švarūs demo duomenys kiekvienam testui: HR persona atstato per API."""
    with httpx.Client(base_url=server, timeout=30) as http:
        hr = next(p for p in http.get("/api/hr/demo/personas").json() if "hr" in p["roles"])
        me = http.post("/api/hr/demo/login", json={"employee_id": hr["id"]})
        assert me.status_code == 200, me.text
        r = http.post("/api/hr/demo/reset", headers={"X-CSRF-Token": me.json()["csrf"]}, json={})
        assert r.status_code == 200, r.text


@pytest.fixture()
def page(page: Page, server: str) -> Page:
    page.set_viewport_size({"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    assert not errors, f"JS klaidos: {errors}"


# ---------- duomenys ----------

def personas(base: str) -> list[dict]:
    r = httpx.get(f"{base}/api/hr/demo/personas", timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def pick_pair(people: list[dict]) -> tuple[dict, dict]:
    """Eilinis darbuotojas ir pavaduojantis kolega iš to paties skyriaus (abu be vadovo / HR rolės)."""
    plain = [p for p in people if set(p["roles"]) == {"employee"}]
    for emp in plain:
        for sub in plain:
            if sub["id"] != emp["id"] and sub["department"] == emp["department"]:
                return emp, sub
    raise AssertionError("Demo duomenyse nėra dviejų eilinių darbuotojų viename skyriuje")


def hr_persona(people: list[dict]) -> dict:
    return next(p for p in people if "hr" in p["roles"])


def next_workdays(start_in_days: int = 35) -> tuple[str, str]:
    """Pirmadienis ir antradienis po ~5 savaičių: ateityje, be savaitgalių, testai nepersidengia."""
    d = date.today() + timedelta(days=start_in_days)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def num(text: str) -> float:
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    assert m, f"Skaičius nerastas: {text!r}"
    return float(m.group(0).replace(",", "."))


# ---------- UI žingsniai ----------

def view(page: Page):
    """Šiuo metu rodomas ekranas (paslėpti ekranai DOM'e lieka)."""
    return page.locator(".view.on")


def login_as(page: Page, base: str, name: str) -> None:
    page.goto(f"{base}/hr#/mine")
    persona_title = page.get_by_role("heading", name="Prisijungti kaip…")
    expect(page.locator("#boot")).to_be_hidden()  # sesijos patikra baigta: rodomas prisijungimas arba programa
    if page.locator("#app-view").is_visible():
        page.locator("#logout").click()
    expect(persona_title).to_be_visible()
    page.get_by_role("button", name=re.compile(re.escape(name))).click()
    expect(page.locator("#who-name")).to_have_text(name)


def open_tab(page: Page, label: str) -> None:
    page.locator(".tabs").get_by_role("link", name=re.compile(label)).click()


def submit_request(page: Page, *, sub_id: int, date_from: str, date_to: str, trip: dict | None = None) -> float:
    expect(page.get_by_role("heading", name="Mano prašymai")).to_be_visible()
    page.locator("#new-btn").click()
    expect(page.get_by_role("heading", name="Naujas prašymas")).to_be_visible()
    if trip:
        page.locator(".seg label", has_text="Komandiruotė").click()
        expect(page.get_by_label("Kur vykstate")).to_be_visible()
        page.get_by_label("Kur vykstate").fill(trip["destination"])
        page.get_by_label("Tikslas", exact=True).fill(trip["purpose"])
    page.get_by_label("Nuo", exact=True).fill(date_from)
    page.get_by_label("Iki (imtinai)", exact=True).fill(date_to)
    days = page.get_by_test_id("workdays")
    expect(days).to_contain_text("darbo d.")
    page.locator("#substitute").select_option(str(sub_id))
    page.get_by_role("button", name="Pateikti ir pasirašyti").click()
    expect(page.get_by_test_id("sid")).to_be_visible()
    return num(days.inner_text())


def sign_current(page: Page) -> None:
    """Esame demo Smart-ID lange: pasirašom ir laukiam grįžimo į prašymą."""
    expect(page.get_by_text("Demo parašas (imitacija)")).to_be_visible()
    expect(page.get_by_test_id("control-code")).to_have_text(re.compile(r"^\d{4}$"))
    page.get_by_role("button", name="Pasirašyti Smart-ID (demo)").click()
    expect(page.get_by_test_id("sid-wait")).to_be_visible()
    expect(page.get_by_test_id("sid-done")).to_contain_text("Pasirašyta", timeout=SIGN_TIMEOUT)
    expect(page.locator("#req-title")).to_be_visible(timeout=SIGN_TIMEOUT)


def sign_from_queue(page: Page) -> None:
    open_tab(page, "Laukia parašo")
    expect(page.get_by_role("heading", name="Laukia mano parašo")).to_be_visible()
    page.locator("#to-sign-list").get_by_test_id("req-card").first.click()
    sign_current(page)


def run_leave_flow(page: Page, base: str) -> None:
    people = personas(base)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays()

    login_as(page, base, emp["name"])
    balance = page.get_by_test_id("balance")
    expect(balance).to_contain_text(re.compile(r"\d"))
    before = num(balance.inner_text())
    days = submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    assert days >= 1
    sign_current(page)
    expect(page.get_by_test_id("signer-employee")).to_have_attribute("data-state", "signed")
    expect(page.get_by_test_id("signer-substitute")).to_have_attribute("data-state", "current")
    manager = page.get_by_test_id("signer-manager").get_attribute("data-name")
    assert manager

    login_as(page, base, sub["name"])
    expect(page.locator("#sign-count")).to_have_text("1")
    sign_from_queue(page)

    login_as(page, base, manager)
    sign_from_queue(page)
    expect(view(page).get_by_test_id("status")).to_have_text("Patvirtintas")
    expect(view(page).get_by_test_id("pdf-signed").first).to_be_visible()

    login_as(page, base, emp["name"])
    after = f"{before - days:g}".replace(".", ",")
    expect(page.get_by_test_id("balance")).to_have_text(re.compile(rf"^{re.escape(after)}\s*d\.$"))
    card = page.locator("#mine-list").get_by_test_id("req-card").first
    expect(card.get_by_test_id("status")).to_have_text("Patvirtintas")
    card.click()
    page.get_by_test_id("audit").locator("summary").click()
    expect(page.get_by_test_id("audit")).to_contain_text("Pasirašė")


# ---------- testai ----------

def test_leave_flow_three_signatures(page: Page, server: str):
    run_leave_flow(page, server)


def test_not_your_turn_is_disabled(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(42)
    login_as(page, server, emp["name"])
    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    sign_url = page.evaluate("location.hash")
    # Darbuotojo pasirašymo langą atidaro pavaduojantis: ne jo parašas
    login_as(page, server, sub["name"])
    page.goto(f"{server}/hr{sign_url}")
    expect(page.get_by_test_id("not-your-turn")).to_be_visible()
    expect(page.get_by_role("button", name="Pasirašyti Smart-ID (demo)")).to_be_disabled()


def test_decline_flow_requires_reason(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(49)

    login_as(page, server, emp["name"])
    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    sign_current(page)

    login_as(page, server, sub["name"])
    open_tab(page, "Laukia parašo")
    page.locator("#to-sign-list").get_by_test_id("req-card").first.click()
    page.get_by_role("button", name="Atsisakyti").click()
    page.get_by_role("button", name="Patvirtinti atsisakymą").click()
    expect(page.locator("#decline-msg")).to_have_text("Įrašykite atsisakymo priežastį.")
    page.get_by_label(re.compile("Kodėl atsisakote")).fill("Tą savaitę pats atostogauju")
    page.get_by_role("button", name="Patvirtinti atsisakymą").click()
    expect(page.get_by_test_id("sid-done")).to_contain_text("Atsisakyta")
    expect(page.locator("#req-title")).to_be_visible(timeout=SIGN_TIMEOUT)
    expect(view(page).get_by_test_id("status")).to_have_text("Atmestas")
    expect(page.locator(".notice.rejected")).to_contain_text("Tą savaitę pats atostogauju")

    login_as(page, server, emp["name"])
    expect(page.locator("#mine-list").get_by_test_id("status").first).to_have_text("Atmestas")


def test_form_validation_and_cancel(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(56)
    login_as(page, server, emp["name"])
    page.locator("#new-btn").click()
    page.get_by_label("Nuo", exact=True).fill(date_to)
    page.get_by_label("Iki (imtinai)", exact=True).fill(date_from)
    expect(page.get_by_test_id("dates-error")).to_be_visible()
    page.get_by_role("button", name="Pateikti ir pasirašyti").click()
    expect(page.locator("#form-error")).to_contain_text("pavaduojantį")
    page.locator("#req-form").get_by_role("link", name="Atšaukti").click()

    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    page.locator("#sign-body .back").click()
    expect(page.locator("#req-title")).to_be_visible()
    page.get_by_role("button", name="Atšaukti prašymą").click()
    page.locator("#confirm-dialog").get_by_role("button", name="Atšaukti prašymą").click()
    expect(view(page).get_by_test_id("status")).to_have_text("Atšauktas")
    expect(page.get_by_role("button", name="Atšaukti prašymą")).to_have_count(0)


def test_trip_flow_order_signed_and_marked_k(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(63)

    login_as(page, server, emp["name"])
    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to,
                   trip={"destination": "Ryga, tiekėjo sandėlis", "purpose": "Prekių priėmimo mokymai"})
    sign_current(page)
    manager = page.get_by_test_id("signer-manager").get_attribute("data-name")

    login_as(page, server, sub["name"])
    sign_from_queue(page)
    login_as(page, server, manager)
    sign_from_queue(page)
    order = page.get_by_test_id("order-pending")
    expect(order).to_contain_text("Įsakymas laukia parašo")
    order.get_by_role("link", name="Pasirašyti įsakymą").click()
    sign_current(page)
    expect(page.get_by_text("Įsakymas pasirašytas").first).to_be_visible()

    open_tab(page, "Tabelis")
    expect(page.get_by_test_id("timesheet")).to_be_visible()
    target = date.fromisoformat(date_from)
    today = date.today()
    for _ in range((target.year - today.year) * 12 + target.month - today.month):
        current = page.locator("#ts-month").inner_text()
        page.locator("#ts-next").click()
        expect(page.locator("#ts-month")).not_to_have_text(current)
    expect(page.get_by_test_id("timesheet")).to_be_visible()
    cell = page.locator(f'tr[data-employee="{emp["name"]}"] td[data-day="{date_from}"]')
    expect(cell).to_have_text("K")


def test_demo_reset_clears_data(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(70)
    login_as(page, server, emp["name"])
    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    sign_current(page)  # pavaduojančiajam išsiunčiamas kvietimas

    login_as(page, server, hr_persona(people)["name"])
    open_tab(page, "Pašto dėžutė")
    expect(page.get_by_test_id("mail").first).to_be_visible()
    page.get_by_role("button", name="Atstatyti demo duomenis").click()
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Atstatyti").click()
    expect(page.get_by_role("heading", name="Prisijungti kaip…")).to_be_visible()

    login_as(page, server, emp["name"])
    expect(page.get_by_text("Prašymų dar nėra")).to_be_visible()


def test_outbox_link_opens_signing(page: Page, server: str):
    people = personas(server)
    emp, sub = pick_pair(people)
    date_from, date_to = next_workdays(77)
    login_as(page, server, emp["name"])
    submit_request(page, sub_id=sub["id"], date_from=date_from, date_to=date_to)
    sign_current(page)
    login_as(page, server, sub["name"])
    open_tab(page, "Pašto dėžutė")
    page.get_by_test_id("mail").get_by_role("link", name="Atidaryti pasirašymą").first.click()
    expect(page.get_by_test_id("sid")).to_be_visible()
    expect(page.get_by_role("button", name="Pasirašyti Smart-ID (demo)")).to_be_enabled()


def test_mobile_leave_flow(page: Page, server: str):
    page.set_viewport_size({"width": 390, "height": 844})
    run_leave_flow(page, server)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"Horizontalus slinkimas telefone: {overflow}px"
