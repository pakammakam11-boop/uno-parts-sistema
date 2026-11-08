"""Savininko apžvalgos (/) srautai naršyklėje: kiekviena SPEC „Mygtukai ir srautai“ eilutė, desktop ir mobile.
Paleidimas: py -3.12 -m pytest -q tests/e2e/test_dashboard.py

Vienas uvicorn serveris moduliui (HR_DEMO=1, laikina SQLite per HR_DATABASE_URL); prieš kiekvieną testą
POST /api/hr/demo/reset. Prisijungiama per HR demo personas. Jokių sleep: tik expect(...).
DASH_E2E_BASE_URL=http://... leidžia tuos pačius testus prieš jau veikiantį serverį.
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, expect

EXTERNAL = os.environ.get("DASH_E2E_BASE_URL", "").rstrip("/")
ENV = {"HR_DEMO": "1", "RESEND_API_KEY": None, "HR_MAIL_ALLOWLIST": None, "HR_BASE_URL": None, "DATABASE_URL": None}
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}
STATUS_LABELS = {"Demo duomenys", "Prijungta", "Laukia prieigos"}


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
    db = tmp_path_factory.mktemp("dash_e2e") / "hr.db"
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
    """Švarūs HR demo duomenys kiekvienam testui (dashboard duomenys deterministiniai)."""
    with httpx.Client(base_url=server, timeout=30) as http:
        hr = next(p for p in http.get("/api/hr/demo/personas").json() if "hr" in p["roles"])
        me = http.post("/api/hr/demo/login", json={"employee_id": hr["id"]})
        assert me.status_code == 200, me.text
        r = http.post("/api/hr/demo/reset", headers={"X-CSRF-Token": me.json()["csrf"]}, json={})
        assert r.status_code == 200, r.text


@pytest.fixture(params=["desktop", "mobile"])
def viewport(request) -> dict:
    return DESKTOP if request.param == "desktop" else MOBILE


@pytest.fixture()
def page(page: Page, server: str) -> Page:
    page.set_viewport_size(DESKTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    assert not errors, f"JS klaidos: {errors}"


# ---------- pagalbinės ----------

def personas(base: str) -> list[dict]:
    r = httpx.get(f"{base}/api/hr/demo/personas", timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


DIRECTOR_ID = 1  # core.dashboard.connectors.DIRECTOR_ID (HR seed: direktorius)


def director(people: list[dict]) -> dict:
    return next(p for p in people if p["id"] == DIRECTOR_ID)


def plain_employee(people: list[dict]) -> dict:
    return next(p for p in people if set(p["roles"]) == {"employee"})


def money(text: str) -> float:
    """„612 400 €“, „7,73 €“, „−16,7 %“ -> skaičius (lt-LT formatas, NBSP tarpai)."""
    t = text.replace("−", "-").replace(",", ".")
    t = re.sub(r"[^\d.\-]", "", t)
    return float(t)


def login_as(page: Page, base: str, person: dict) -> None:
    r = page.request.post(f"{base}/api/hr/demo/login", data={"employee_id": person["id"]})
    assert r.ok, r.text()


def open_dashboard(page: Page, base: str, size: dict = DESKTOP) -> Page:
    page.set_viewport_size(size)
    login_as(page, base, director(personas(base)))
    page.goto(f"{base}/")
    expect(page.locator("body[data-ready='1']")).to_have_count(1, timeout=15_000)
    expect(page.locator("#kpis")).to_have_attribute("aria-busy", "false")
    return page


def kpi_text(page: Page, key: str) -> str:
    return page.locator(f"[data-testid=kpi-{key}] [data-testid=kpi-value]").inner_text()


def pick_filter(page: Page, testid: str) -> None:
    page.locator(f"[data-testid={testid}]").locator("xpath=..").click()


def no_horizontal_scroll(page: Page) -> None:
    sw, cw = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, f"horizontalus slinkimas: {sw} > {cw}"


# ---------- prisijungimas ir teisės ----------

def test_signed_out_sees_personas_and_director_lands_on_dashboard(page: Page, server: str, viewport: dict):
    page.set_viewport_size(viewport)
    page.goto(f"{server}/")
    expect(page.locator("#login-view")).to_be_visible()
    boss = director(personas(server))
    page.locator(f"[data-testid=persona][data-persona='{boss['id']}']").click()
    expect(page).to_have_url(re.compile(r"/(\?.*)?$"))
    expect(page.locator("[data-testid=kpi-sales] [data-testid=kpi-value]")).to_contain_text("€", timeout=15_000)
    expect(page.locator("#who-name")).to_have_text(boss["name"])
    no_horizontal_scroll(page)


def test_plain_employee_is_redirected_to_hr(page: Page, server: str):
    login_as(page, server, plain_employee(personas(server)))
    page.goto(f"{server}/")
    expect(page).to_have_url(re.compile(r"/hr"), timeout=15_000)


def test_logout_returns_to_login(page: Page, server: str):
    open_dashboard(page, server)
    page.locator("#logout").click()
    expect(page.locator("#login-view")).to_be_visible()
    expect(page.locator("[data-testid=persona]").first).to_be_visible()


# ---------- viršus: laikotarpis ir filialas ----------

def test_period_and_branch_recalculate(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    seen = {"month": (kpi_text(page, "sales"), page.locator("[data-testid=costs-total]").inner_text())}
    ranges = {page.locator("[data-testid=range-line]").inner_text()}
    for period in ["quarter", "week"]:  # savaitė ar ketvirtis gali sutapti su mėnesiu (šventės, ketvirčio pradžia), abu kartu ne
        before = page.locator("[data-testid=range-line]").inner_text()
        pick_filter(page, f"period-{period}")
        expect(page).to_have_url(re.compile(f"period={period}"))
        expect(page.locator("[data-testid=range-line]")).not_to_have_text(before)
        expect(page.locator("#kpis")).to_have_attribute("aria-busy", "false")
        ranges.add(page.locator("[data-testid=range-line]").inner_text())
        seen[period] = (kpi_text(page, "sales"), page.locator("[data-testid=costs-total]").inner_text())
    assert len(ranges) == 3
    assert len({v[0] for v in seen.values()}) >= 2, f"pardavimai nepasikeitė: {seen}"
    assert len({v[1] for v in seen.values()}) >= 2, f"kaštai nepasikeitė: {seen}"

    pick_filter(page, "period-quarter")
    expect(page.locator("[data-testid=kpi-sales] [data-testid=kpi-value]")).to_have_text(seen["quarter"][0])
    pick_filter(page, "branch-kns")
    expect(page).to_have_url(re.compile(r"branch=kns"))
    expect(page.locator("[data-testid=kpi-sales] [data-testid=kpi-value]")).not_to_have_text(seen["quarter"][0])
    assert money(kpi_text(page, "sales")) < money(seen["quarter"][0]), "vienas filialas negali parduoti daugiau nei visi"

    page.reload()  # filtrai išlieka URL
    expect(page.locator("[data-testid=period-quarter]")).to_be_checked()
    expect(page.locator("[data-testid=branch-kns]")).to_be_checked()
    no_horizontal_scroll(page)


def test_forecast_how_calculated(page: Page, server: str):
    open_dashboard(page, server)
    how = page.locator("[data-testid=how]")
    expect(how).to_be_hidden()
    page.locator("[data-testid=how-btn]").click()
    expect(how).to_be_visible()
    expect(how).to_contain_text("4 savaičių")
    expect(how.locator(".how-table tbody tr").first).to_be_visible()
    expect(page.locator("[data-testid=how-btn]")).to_have_attribute("aria-expanded", "true")


# ---------- veiksmų eilė ----------

def test_action_jumps_to_section(page: Page, server: str):
    open_dashboard(page, server)
    actions = page.locator("[data-testid=action]")
    expect(actions.first).to_be_visible()
    assert 3 <= actions.count() <= 6, f"veiksmų turi būti 3-6, yra {actions.count()}"
    internal = page.locator("[data-testid=action][data-link^='#']").first
    target = internal.get_attribute("data-link").lstrip("#").split("/")[0]
    internal.click()
    expect(page.locator(f"#{target}")).to_be_in_viewport()
    expect(page).to_have_url(re.compile(f"#{target}"))


def hr_session(base: str, employee_id: int) -> tuple[httpx.Client, str]:
    http = httpx.Client(base_url=base, timeout=30)
    me = http.post("/api/hr/demo/login", json={"employee_id": employee_id})
    assert me.status_code == 200, me.text
    return http, me.json()["csrf"]


def sign_next(http: httpx.Client, csrf: str, request_id: int) -> None:
    data = http.get(f"/api/hr/requests/{request_id}").json()
    url = data["sign_url"] or data["my_sign_url"]
    _, _, _, token, signer_id = url.split("/")  # /hr/sign/{token}/{signer_id}
    r = http.post(f"/api/hr/sign/{token}/{signer_id}", json={"action": "sign"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text


def request_waiting_for_director(base: str) -> None:
    """Sandėlio vadovė (vadovas = direktorius) prašo atostogų, ji ir pavaduojantis pasirašo: lieka direktoriaus parašas."""
    people = personas(base)
    boss = director(people)
    applicant = next(p for p in people if p["id"] != boss["id"] and "manager" in p["roles"] and p["department"] == "Sandėlis")
    substitute = next(p for p in people if set(p["roles"]) == {"employee"} and p["department"] == applicant["department"])
    http, csrf = hr_session(base, applicant["id"])
    try:
        r = http.post("/api/hr/requests", headers={"X-CSRF-Token": csrf}, json={
            "type": "leave", "leave_kind": "annual", "substitute_id": substitute["id"],
            "date_from": "2027-02-01", "date_to": "2027-02-05"})
        assert r.status_code in (200, 201), r.text
        rid = r.json()["id"]
        sign_next(http, csrf, rid)
    finally:
        http.close()
    http, csrf = hr_session(base, substitute["id"])
    try:
        sign_next(http, csrf, rid)
    finally:
        http.close()


def test_action_to_hr_opens_hr(page: Page, server: str):
    request_waiting_for_director(server)
    open_dashboard(page, server)
    hr_action = page.locator("[data-testid=action][data-link='/hr']")
    expect(hr_action).to_have_count(1)
    expect(hr_action).to_contain_text("parašo")
    expect(page.locator("[data-testid=ops-hr]")).to_contain_text("1")
    hr_action.click()
    expect(page).to_have_url(re.compile(r"/hr"))


# ---------- kaštai: grupė -> tiekėjas -> dokumentas ----------

def test_costs_drilldown_and_back(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    groups = page.locator("[data-testid=cost-group]")
    expect(groups.first).to_be_visible()
    group_name = groups.first.locator("b").inner_text()
    groups.first.click()

    suppliers = page.locator("[data-testid=cost-supplier]")
    expect(suppliers.first).to_be_visible()
    expect(page.locator("#costs-crumbs")).to_contain_text(group_name)
    suppliers.first.click()

    expect(page.locator("[data-testid=cost-doc]").first).to_be_visible()
    assert page.locator("[data-testid=cost-doc]").count() >= 1

    page.locator("[data-testid=costs-back]").click()
    expect(suppliers.first).to_be_visible()
    page.locator("[data-testid=costs-back]").click()
    expect(groups.first).to_be_visible()
    expect(page.locator("#costs-crumbs")).to_be_hidden()
    no_horizontal_scroll(page)


# ---------- efektyvumas ----------

def column_values(page: Page, col: int) -> list[float]:
    cells = page.locator("#emp-table tbody tr").locator(f"xpath=./*[{col}]")
    return [money(t) for t in cells.all_inner_texts()]


def test_employee_ranking_sorts(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    expect(page.locator("[data-testid=emp-row]").first).to_be_visible()
    profit = column_values(page, 2)
    assert profit == sorted(profit, reverse=True), "numatytai rikiuojama pagal pelną"
    names_by_profit = page.locator("[data-testid=emp-row] .emp-n").all_inner_texts()

    page.locator("[data-testid=sort-perHour]").click()
    expect(page.locator("th:has([data-testid=sort-perHour])")).to_have_attribute("aria-sort", "descending")
    per_hour = column_values(page, 3)
    assert per_hour == sorted(per_hour, reverse=True)
    names_by_hour = page.locator("[data-testid=emp-row] .emp-n").all_inner_texts()
    assert names_by_hour != names_by_profit, "eilių tvarka turi pasikeisti"

    page.locator("[data-testid=sort-profit]").click()
    expect(page.locator("[data-testid=emp-row] .emp-n").first).to_have_text(names_by_profit[0])


# ---------- darbai ----------

def test_email_priority_filter(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    emails = page.locator("[data-testid=email]")
    expect(emails.first).to_be_visible()
    total = emails.count()
    page.locator("[data-testid=prio-high]").click()
    expect(page.locator("[data-testid=prio-high]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-testid=email]:not([data-prio=high])")).to_have_count(0)
    assert page.locator("[data-testid=email]").count() < total
    page.locator("[data-testid=prio-all]").click()
    expect(emails).to_have_count(total)


# ---------- operacijos ir navigacija ----------

def test_operations_hr_requests_open_hr(page: Page, server: str):
    open_dashboard(page, server)
    expect(page.locator("[data-testid=ops-calls]")).to_contain_text("konversija 35 %")
    page.locator("[data-testid=ops-hr-link]").click()
    expect(page).to_have_url(re.compile(r"/hr"))


def test_costs_note_under_costs(page: Page, server: str):
    open_dashboard(page, server)
    expect(page.locator("[data-testid=costs-note]")).to_be_visible()
    expect(page.locator("[data-testid=costs-note]")).to_contain_text("dokumentų datas")


def test_no_dashes_in_visible_text(page: Page, server: str):
    open_dashboard(page, server)
    text = page.locator("body").inner_text()
    assert "\u2013" not in text and "\u2014" not in text, "matomame tekste neturi būti ilgų brūkšnių"


def test_operations_accounting_link(page: Page, server: str):
    open_dashboard(page, server)
    expect(page.locator("[data-testid=ops-accounting-link]")).to_have_attribute("href", "/apskaita")


def test_shared_navigation(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    expect(page.locator("[data-testid=nav-overview]")).to_have_attribute("aria-current", "page")
    page.locator("[data-testid=nav-accounting]").click()
    expect(page).to_have_url(re.compile(r"/apskaita$"))
    page.go_back()
    expect(page.locator("body[data-ready='1']")).to_have_count(1, timeout=15_000)
    page.locator("[data-testid=nav-hr]").click()
    expect(page).to_have_url(re.compile(r"/hr"))
    page.goto(f"{server}/apskaita")
    page.goto(f"{server}/")
    expect(page.locator("[data-testid=nav-overview]")).to_be_visible()


# ---------- šaltiniai ir žymos ----------

def test_sources_list_with_status(page: Page, server: str, viewport: dict):
    open_dashboard(page, server, viewport)
    page.locator(".jump a[href='#saltiniai']").click()
    rows = page.locator("[data-testid=source]")
    expect(rows.first).to_be_in_viewport()
    assert rows.count() >= 3
    for label in rows.locator("[data-testid=status-chip]").all_inner_texts():
        assert label.strip() in STATUS_LABELS
    expect(page.locator("[data-testid=source]").filter(has_text="Laukia prieigos").first).to_be_visible()
    expect(page.locator("[data-testid=source]").filter(has_text="icapi@intercars.eu")).to_have_count(1)
    no_horizontal_scroll(page)


def test_every_number_card_shows_source_date_and_status(page: Page, server: str):
    open_dashboard(page, server)
    for testid in ["kpi-sales", "kpi-gross_profit", "kpi-costs", "kpi-net", "kpi-forecast", "costs", "sales-chart", "budget",
                   "eff-figs", "employees", "manufacturers", "branches", "emails", "tasks", "ops-calls", "ops-deposits",
                   "ops-returns", "ops-routes", "ops-hr", "ops-accounting"]:
        meta = page.locator(f"[data-testid={testid}] [data-testid=meta]").first
        expect(meta, f"{testid}: nėra meta").to_be_visible()
        expect(meta.locator("time"), f"{testid}: nėra „duomenys iki“").to_contain_text("duomenys iki")
        assert meta.locator("[data-testid=status-chip]").inner_text().strip() in STATUS_LABELS, testid


def test_mobile_full_page_has_no_horizontal_scroll(page: Page, server: str):
    open_dashboard(page, server, MOBILE)
    expect(page.locator("[data-testid=nav-hr]")).to_be_visible()  # apatinė navigacija nykščiui
    for anchor in ["#finansai", "#efektyvumas", "#darbai", "#operacijos", "#saltiniai"]:
        page.locator(anchor).scroll_into_view_if_needed()
        no_horizontal_scroll(page)


# ---------- peržiūros pataisymai ----------

def test_filter_change_resets_cost_drilldown(page: Page, server: str):
    open_dashboard(page, server)
    page.locator("[data-testid=cost-group]").first.click()
    page.locator("[data-testid=cost-supplier]").first.click()
    expect(page.locator("[data-testid=cost-doc]").first).to_be_visible()
    pick_filter(page, "period-day")
    expect(page.locator("#costs-crumbs")).to_be_hidden()
    expect(page.locator("[data-testid=cost-group]").first).to_be_visible()
    expect(page.locator("#costs-body .state.err")).to_have_count(0)


def test_calls_card_names_the_last_trading_day(page: Page, server: str):
    open_dashboard(page, server)
    card = page.locator("[data-testid=ops-calls]")
    expect(card).to_contain_text(re.compile(r"Paskutinė prekybos diena \(\d{4}-\d{2}-\d{2}\)"))
    expect(card).not_to_contain_text("Vakar praleisti")


def test_shared_navigation_on_accounting_and_hr(page: Page, server: str):
    open_dashboard(page, server)
    page.goto(f"{server}/apskaita")
    expect(page.locator("[data-testid=nav-accounting]")).to_have_attribute("aria-current", "page")
    page.locator("[data-testid=nav-overview]").click()
    expect(page.locator("body[data-ready='1']")).to_have_count(1, timeout=15_000)
    page.goto(f"{server}/hr")
    expect(page.locator("[data-testid=nav-hr]")).to_have_attribute("aria-current", "page")
    page.locator("[data-testid=nav-accounting]").click()
    expect(page).to_have_url(re.compile(r"/apskaita$"))
