"""Gamybinio UI (/app) srautai naršyklėje. Paleidimas: py -3.12 -m pytest -q tests/e2e/test_prod.py

Kiekvienas testas gauna savo uvicorn serverį su atskira laikina DB. Būsena paruošiama per HTTP API (httpx),
tikrinama per UI (Playwright). Jokių sleep: tik expect(...) laukimai.
"""
from __future__ import annotations

import io
import socket
import zipfile
import threading
import time
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import uvicorn
from openpyxl import Workbook, load_workbook
from playwright.sync_api import Page, expect

from app import auth, main
from core import employees as emp, world
from core.rivile_payroll import DEFAULT_PAY_MAPPING
from data import gen_timesheet

PASSWORD = "labai-ilgas-slaptazodis"
USER = "buhaltere"
OTHER_USER = "kita-imone"
PERIOD = "2026-10"
INJECTED = sorted(a.tab_no for a in world.TIMESHEET_ANOMALIES)  # D004, D017, D029, D041, D050


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def demo_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("prod_demo")
    files = gen_timesheet.main(out)
    files["employees"] = emp.write_employees_xlsx(emp.from_world(), out / "darbuotojai.xlsx")
    return files


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path, monkeypatch) -> str:
    """uvicorn su izoliuota DB: auth.settings.db_path ir UNO_DB rodo į tą patį laikiną failą."""
    db = tmp_path / "uno.db"
    monkeypatch.setattr(auth.settings, "db_path", db)
    monkeypatch.setenv("UNO_DB", str(db))
    monkeypatch.delattr(main.app.state, "uno_store", raising=False)
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, USER, PASSWORD, "accountant")
        auth.create_user(conn, 2, OTHER_USER, PASSWORD, "accountant")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "uvicorn nepasileido"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page(page: Page, server: str) -> Page:
    page.set_viewport_size({"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    assert not errors, f"JS klaidos: {errors}"


class Api:
    """Duomenų paruošimas per HTTP be naršyklės."""

    def __init__(self, base: str, username: str = USER):
        self.http = httpx.Client(base_url=base, timeout=60)
        r = self.http.post("/api/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.headers = {"X-CSRF-Token": r.json()["csrf"]}

    def upload(self, kind: str, path: Path, period: str = PERIOD) -> httpx.Response:
        with open(path, "rb") as fh:
            return self.http.post(f"/api/v1/periods/{period}/uploads/{kind}", headers=self.headers,
                                  files={"file": (path.name, fh, "application/octet-stream")})

    def upload_inputs(self, files: dict) -> None:
        for kind, key in (("employees", "employees"), ("timesheet", "tabelis"), ("rivile_hours", "rivile"),
                          ("sick_certs", "sodra"), ("vacation_requests", "requests")):
            assert self.upload(kind, files[key]).status_code == 200

    def post(self, action: str) -> dict:
        r = self.http.post(f"/api/v1/periods/{PERIOD}/{action}", headers=self.headers)
        assert r.status_code == 200, r.text
        return r.json()

    def close(self) -> None:
        self.http.close()


@pytest.fixture()
def api(server):
    client = Api(server)
    yield client
    client.close()


@pytest.fixture()
def checked(api, demo_files):
    """Failai įkelti ir patikra paleista (be UI)."""
    api.upload_inputs(demo_files)
    return api, api.post("check")


# ---------- UI pagalbinės ----------

def ui_login(page: Page, server: str, username: str = USER, password: str = PASSWORD) -> None:
    page.goto(f"{server}/app")
    page.locator("#login-user").fill(username)
    page.locator("#login-pass").fill(password)
    page.get_by_role("button", name="Prisijungti").click()


def open_app(page: Page, server: str, username: str = USER, period: str = PERIOD) -> None:
    ui_login(page, server, username)
    expect(page.locator("#app-view")).to_be_visible()
    set_period(page, period)


def set_period(page: Page, value: str) -> None:
    page.locator("#period").fill(value)
    expect(page.locator("#period")).to_have_value(value)


def goto_step(page: Page, name: str) -> None:
    page.locator(f'.steps button[data-view="{name}"]').click()
    expect(page.locator(f"#view-{name}")).to_have_class("view on")


def upload_row(page: Page, kind: str):
    return page.locator("#uploads li").filter(has=page.locator(f'input[data-kind="{kind}"]'))


def ui_upload(page: Page, kind: str, path: Path) -> None:
    page.locator(f'input[data-kind="{kind}"]').set_input_files(str(path))


def write_rivile_payroll(path: Path, expected: dict, tweaks: dict) -> None:
    """Rivilės DU eksportas iš mūsų tikėtinų reikšmių; tweaks[(tab, laukas)] = pridedama suma."""
    fields = ["id", "gross", "npd", "gpm", "sodra_employee", "sodra_employer", "net"]
    wb = Workbook()
    ws = wb.active
    ws.append(["Rivilė DU ataskaita"])
    ws.append([DEFAULT_PAY_MAPPING[f] for f in fields])
    for tab, e in expected.items():
        ws.append([tab] + [float(D(e[f]) + tweaks.get((tab, f), D(0))) for f in fields[1:]])
    wb.save(path)


def ui_check(page: Page) -> None:
    goto_step(page, "check")
    page.locator("#run-check").click()
    expect(page.locator("#check-kpis .kpi")).to_have_count(5)


def assert_no_overflow(page: Page, view: str) -> None:
    widths = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert widths[0] <= widths[1], f"{view}: horizontalus overflow {widths[0]} > {widths[1]}"


# ---------- testai ----------

def test_wrong_password_shows_lithuanian_error(page: Page, server: str):
    ui_login(page, server, USER, "neteisingas-slaptazodis")
    expect(page.locator("#login-error")).to_have_text("Neteisingas naudotojo vardas arba slaptažodis")
    expect(page.locator("#login-view")).to_be_visible()
    expect(page.locator("#app-view")).to_be_hidden()


def test_login_ok_shows_app_with_user_name(page: Page, server: str):
    ui_login(page, server)
    expect(page.locator("#app-view")).to_be_visible()
    expect(page.locator("#login-view")).to_be_hidden()
    expect(page.locator("#who")).to_have_text(f"{USER} (buhalterė)")
    page.reload()  # sesija išlieka po perkrovimo
    expect(page.locator("#who")).to_have_text(f"{USER} (buhalterė)")


def test_period_defaults_to_previous_month_and_switching_works(page: Page, server: str, api: Api, demo_files):
    api.upload("employees", demo_files["employees"])
    previous = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    ui_login(page, server)
    expect(page.locator("#period")).to_have_value(previous)
    set_period(page, PERIOD)
    expect(upload_row(page, "employees")).to_contain_text("darbuotojai.xlsx")
    set_period(page, "2026-09")  # kitas laikotarpis be failų
    expect(upload_row(page, "employees")).not_to_contain_text("darbuotojai.xlsx")
    set_period(page, PERIOD)
    expect(upload_row(page, "employees")).to_contain_text("darbuotojai.xlsx")


def test_upload_employees_and_timesheet_marks_check_and_required_badges(page: Page, server: str, demo_files):
    open_app(page, server)
    for kind in ("employees", "timesheet"):
        row = upload_row(page, kind)
        expect(row.locator(".req")).to_have_text("privaloma")
        expect(row.locator("i.ph-warning-circle")).to_have_count(1)
    expect(upload_row(page, "rivile_hours").locator(".req")).to_have_count(0)
    expect(upload_row(page, "rivile_hours").locator("i.ph-circle-dashed")).to_have_count(1)
    ui_upload(page, "employees", demo_files["employees"])
    expect(page.locator("#toast")).to_contain_text("Įkelta: darbuotojai.xlsx")
    expect(upload_row(page, "employees").locator("i.ph-check-circle")).to_have_count(1)
    expect(page.locator('[data-badge="files"]')).to_have_text("")  # dar trūksta tabelio
    ui_upload(page, "timesheet", demo_files["tabelis"])
    expect(upload_row(page, "timesheet").locator("i.ph-check-circle")).to_have_count(1)
    expect(upload_row(page, "timesheet")).to_contain_text(USER)
    expect(upload_row(page, "timesheet").locator(".req")).to_have_text("privaloma")
    expect(page.locator('[data-badge="files"]')).to_have_text("OK")


def test_wrong_file_type_shows_error_toast(page: Page, server: str, demo_files):
    open_app(page, server)
    ui_upload(page, "employees", demo_files["sodra"])  # .csv vietoj .xlsx
    expect(page.locator("#toast")).to_contain_text("Tinka tik .xlsx")
    expect(page.locator("#toast")).to_have_class("toast on err")
    expect(upload_row(page, "employees").locator("i.ph-warning-circle")).to_have_count(1)


def test_check_shows_injected_tab_numbers_kpis_and_review_list(page: Page, server: str, api: Api, demo_files):
    api.upload_inputs(demo_files)
    open_app(page, server)
    ui_check(page)
    kpi = page.locator("#check-kpis .kpi").filter(has_text="Darbuotojų")
    expect(kpi.locator("b")).to_have_text("57")
    findings = page.locator("#check-findings .finding")
    for tab in INJECTED:
        expect(findings.locator(".ref", has_text=tab).first).to_be_visible()
    expect(page.locator("#check-unsupported li").first).to_be_visible()
    expect(page.locator("#check-expected tbody tr").first).to_be_visible()
    expect(page.locator('[data-badge="check"]')).not_to_have_text("")


def test_download_disabled_before_approval(page: Page, server: str, checked):
    open_app(page, server)
    goto_step(page, "approve")
    expect(page.locator("#approve-status")).to_contain_text("Nepatvirtinta")
    expect(page.locator("#download-rivile")).to_be_disabled()


def test_approve_then_download_rivile_xlsx(page: Page, server: str, checked, tmp_path):
    open_app(page, server)
    goto_step(page, "approve")
    page.locator("#approve-btn").click()
    expect(page.locator("#approve-status")).to_contain_text(f"Patvirtino {USER}")
    expect(page.locator("#download-rivile")).to_be_enabled()
    with page.expect_download() as info:
        page.locator("#download-rivile").click()
    download = info.value
    # demo tabelyje yra viršvalandžių: ZIP su tabeliu ir atskiru viršvalandžių sąrašu
    assert download.suggested_filename.endswith(".zip")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    zf = zipfile.ZipFile(saved)
    assert any(n.startswith("rivile_virsvalandziai_") for n in zf.namelist())
    tabelis = next(n for n in zf.namelist() if n.startswith("rivile_tabelis"))
    wb = load_workbook(io.BytesIO(zf.read(tabelis)))
    assert {"Timesheet_operations", "Lines"} <= set(wb.sheetnames)
    assert len(list(wb["Timesheet_operations"].iter_rows(min_row=2, values_only=True))) == 57
    expect(page.locator("#toast")).to_contain_text("Viršvalandžiai atskirame faile")


def test_reupload_timesheet_revokes_approval(page: Page, server: str, checked, demo_files):
    api, _ = checked
    api.post("approve")
    open_app(page, server)
    goto_step(page, "approve")
    expect(page.locator("#approve-status")).to_contain_text(f"Patvirtino {USER}")
    expect(page.locator("#download-rivile")).to_be_enabled()
    goto_step(page, "files")
    ui_upload(page, "timesheet", demo_files["tabelis"])
    expect(page.locator("#toast")).to_contain_text("Įkelta")
    goto_step(page, "approve")
    expect(page.locator("#approve-status")).to_contain_text("Nepatvirtinta")
    expect(page.locator("#download-rivile")).to_be_disabled()


def test_rivile_payroll_compare_shows_two_diffs_and_totals(page: Page, server: str, checked, tmp_path):
    api, result = checked
    api.post("approve")  # palyginimas leidžiamas tik su patvirtinta patikra (S4)
    supported = [t for t, e in result["expected"].items() if e["supported"]]
    path = tmp_path / "rivile_du.xlsx"
    write_rivile_payroll(path, result["expected"],
                         {(supported[0], "net"): D("5.00"), (supported[1], "gpm"): D("3.00")})
    open_app(page, server)
    ui_upload(page, "rivile_payroll", path)
    expect(upload_row(page, "rivile_payroll").locator("i.ph-check-circle")).to_have_count(1)
    goto_step(page, "compare")
    page.locator("#run-compare").click()
    totals = page.locator("#compare-totals .total")
    expect(totals).to_have_count(5)
    expect(totals.filter(has_text="GPM313 (visas")).to_be_visible()
    expect(totals.filter(has_text="GPM, patikrinta")).to_contain_text("3,00")
    expect(totals.filter(has_text="Sodra, patikrinta")).to_contain_text("sutampa")
    expect(totals.filter(has_text="Būsena")).to_contain_text("patikrinta")
    per_employee = page.locator("#compare-findings .finding").filter(
        has_not=page.locator(".ref", has_text="TOTAL"))
    expect(per_employee).to_have_count(2)
    expect(per_employee.locator(".ref", has_text=supported[0])).to_have_count(1)
    expect(per_employee.locator(".ref", has_text=supported[1])).to_have_count(1)
    expect(page.locator("#compare-review li").first).to_be_visible()


def test_audit_view_lists_upload_approve_export(page: Page, server: str, checked):
    api, _ = checked
    api.post("approve")
    assert api.http.get(f"/api/v1/periods/{PERIOD}/rivile-timesheet").status_code == 200
    open_app(page, server)
    goto_step(page, "audit")
    rows = page.locator("#audit tbody tr")
    expect(rows.filter(has_text="Įkėlė")).to_have_count(5)
    expect(rows.filter(has_text="Paleido")).to_have_count(1)
    expect(rows.filter(has_text="Patvirtino")).to_have_count(1)
    expect(rows.filter(has_text="Atsisiuntė")).to_have_count(1)
    expect(rows.first).to_contain_text(USER)


def test_logout_returns_to_login_and_api_is_401(page: Page, server: str):
    open_app(page, server)
    page.locator("#logout").click()
    expect(page.locator("#login-view")).to_be_visible()
    expect(page.locator("#app-view")).to_be_hidden()
    assert page.request.get(f"{server}/api/v1/periods/{PERIOD}/state").status == 401
    assert page.request.get(f"{server}/api/me").status == 401


def test_second_company_sees_empty_state_for_same_period(page: Page, server: str, checked):
    api, _ = checked
    api.post("approve")
    open_app(page, server, OTHER_USER)
    expect(page.locator("#uploads li")).to_have_count(6)
    expect(page.locator("#uploads li i.ph-check-circle")).to_have_count(0)
    expect(page.locator("#uploads li .file-btn", has_text="Pakeisti")).to_have_count(0)
    expect(page.locator('[data-badge="files"]')).to_have_text("")
    goto_step(page, "approve")
    expect(page.locator("#approve-status")).to_contain_text("Nepatvirtinta. Pirma paleiskite patikrą")
    expect(page.locator("#download-rivile")).to_be_disabled()
    goto_step(page, "audit")
    expect(page.locator("#audit")).to_contain_text("Įrašų dar nėra")


@pytest.mark.parametrize("width", [375, 1280])
def test_no_horizontal_overflow_on_files_and_check_views(page: Page, server: str, api: Api, demo_files, width: int):
    api.upload_inputs(demo_files)
    page.set_viewport_size({"width": width, "height": 800})
    open_app(page, server)
    expect(upload_row(page, "timesheet").locator("i.ph-check-circle")).to_have_count(1)
    assert_no_overflow(page, "files")
    ui_check(page)
    expect(page.locator("#check-findings .finding").first).to_be_visible()
    assert_no_overflow(page, "check")
