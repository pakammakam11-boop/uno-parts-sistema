"""Mygtukų ir srautų patikra naršyklėje (Playwright). Paleidimas: py -3.12 -m pytest -q tests/e2e

Kiekvienas UI mygtukas iš demo scenarijaus turi testą. LLM tik peržiūri krentančius.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from app.main import RESULTS, app

PORT = 8781
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def server():
    RESULTS.clear()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture()
def page(page: Page) -> Page:
    page.set_viewport_size({"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    assert not errors, f"JS klaidos: {errors}"


def run_all(page: Page) -> None:
    page.goto(f"{BASE}/apskaita")
    page.get_by_role("button", name="Paleisti uždarymą").click()
    expect(page.locator("#toast")).to_contain_text("Uždarymas paskaičiuotas", timeout=60_000)


def test_overview_ledger_and_total(page: Page):
    page.goto(f"{BASE}/apskaita")
    expect(page.locator("#ledger tbody tr")).to_have_count(7)
    expect(page.locator("#ledger tfoot")).to_contain_text("2 000")
    expect(page.locator("#checklist li")).to_have_count(6)


def test_run_all_fills_every_ledger_row_and_badges(page: Page):
    run_all(page)
    for cell in page.locator("#ledger tbody td.result-cell").all():
        expect(cell).not_to_have_text("Dar nepaleista")
    for key in ("invoices", "bank", "payroll", "control"):
        expect(page.locator(f'[data-badge="{key}"]')).not_to_be_empty()
    bank = page.locator('[data-badge="bank"]')
    expect(bank.locator(".badge.err")).to_be_visible()
    expect(bank.locator(".badge.warn")).to_be_visible()


def test_declarations_view_shows_table(page: Page):
    run_all(page)
    page.locator('.steps button[data-view="declarations"]').click()
    sec = page.locator("#view-declarations")
    expect(sec.locator("table.data tbody tr")).to_have_count(4)
    expect(sec.locator("table.data")).to_contain_text("GPM313")


@pytest.mark.parametrize("view,heading", [
    ("invoices", "Pirkimo ir pardavimo sąskaitos"),
    ("bank", "Banko išrašas ir mokėjimai"),
    ("payroll", "Darbo užmokestis ir darbo laikas"),
    ("control", "Kontrolė, trūkstami dokumentai ir AI"),
    ("declarations", "Mėnesinės deklaracijos"),
    ("overview", "Spalio mėnesio uždarymas"),
])
def test_nav_buttons_switch_views(page: Page, view: str, heading: str):
    page.goto(f"{BASE}/apskaita")
    page.locator(f'.steps button[data-view="{view}"]').click()
    expect(page.locator(f"#view-{view} h1")).to_have_text(heading)
    expect(page.locator(f'.steps button[data-view="{view}"]')).to_have_attribute("aria-current", "page")
    expect(page).to_have_url(re.compile(f"#{view}$"))


def test_hash_link_opens_module(page: Page):
    page.goto(f"{BASE}/apskaita#bank")
    expect(page.locator("#view-bank h1")).to_be_visible()


def test_invoices_run_findings_filters_downloads(page: Page):
    page.goto(f"{BASE}/apskaita#invoices")
    page.locator('[data-run="invoices"]').click()
    sec = page.locator("#view-invoices")
    expect(sec.locator(".finding.error")).not_to_have_count(0, timeout=30_000)
    expect(sec.locator(".finding").first).to_contain_text("PVM")
    sec.locator('[data-filter="error"]').click()
    expect(sec.locator(".finding.warning:visible")).to_have_count(0)
    sec.locator('[data-filter="all"]').click()
    links = sec.locator("[data-downloads] a")
    expect(links).to_have_count(4)
    for i in range(4):
        resp = page.request.get(BASE + links.nth(i).get_attribute("href"))
        assert resp.ok and len(resp.body()) > 500
    expect(sec.locator("[data-table] tbody tr")).to_have_count(60)


def test_finding_suggested_action_expands(page: Page):
    page.goto(f"{BASE}/apskaita#bank")
    page.locator('[data-run="bank"]').click()
    first = page.locator("#view-bank .finding details").first
    expect(first).to_be_visible(timeout=30_000)
    first.locator("summary").click()
    expect(first.locator("pre")).to_contain_text("Sveiki")


def test_bank_run_and_payment_file(page: Page):
    page.goto(f"{BASE}/apskaita#bank")
    page.locator('[data-run="bank"]').click()
    sec = page.locator("#view-bank")
    expect(sec.locator("[data-kpis]")).to_contain_text("Neapmokėtos sąskaitos", timeout=30_000)
    href = sec.locator("[data-downloads] a").first.get_attribute("href")
    xml = page.request.get(BASE + href).text()
    assert "pain.001.001.03" in xml and "CtrlSum" in xml


def test_payroll_calculator_live(page: Page):
    page.goto(f"{BASE}/apskaita#payroll")
    net = page.locator("#calc-net")
    net.fill("846.96")
    expect(page.locator("#calc-out")).to_contain_text("846,96 €")
    expect(page.locator("#calc-out")).to_contain_text("1 15")  # bruto apie 1 153
    page.locator("#calc-term").select_option("1")
    expect(page.locator("#calc-out")).to_contain_text("Sodra darbdavio")
    net.fill("")  # tuščias laukas: jokio NaN ar klaidos
    expect(page.locator("#calc-out")).to_contain_text("0,00 €")
    expect(page.locator("#calc-out")).not_to_contain_text("NaN")


def test_payroll_calculator_does_not_overlap_breakdown(page: Page):
    page.goto(f"{BASE}/apskaita#payroll")
    expect(page.locator("#calc-out")).to_contain_text("Į rankas")
    for el in ("#calc-net", "#calc-term", ".calc .field small"):
        box = page.locator(el).first.bounding_box()
        table = page.locator("#calc-out").bounding_box()
        assert box["x"] + box["width"] <= table["x"], f"{el} užlipa ant lentelės"


def test_payroll_run_shows_57_and_anomalies(page: Page):
    page.goto(f"{BASE}/apskaita#payroll")
    page.locator('[data-run="payroll"]').click()
    sec = page.locator("#view-payroll")
    expect(sec.locator("[data-table] tbody tr")).to_have_count(57, timeout=30_000)
    for tab in ("D004", "D017", "D029", "D041", "D050"):
        expect(sec.locator("[data-findings]")).to_contain_text(tab)


def test_upload_valid_invoice_pdf(page: Page):
    pdf = next((ROOT / "data" / "generated" / "saskaitos").glob("*.pdf"))
    page.goto(f"{BASE}/apskaita#invoices")
    page.locator('[data-upload="invoices"]').set_input_files(str(pdf))
    expect(page.locator("#toast")).to_contain_text("Apdorota", timeout=30_000)
    expect(page.locator("#view-invoices [data-table] tbody tr")).to_have_count(1)


def test_upload_corrupt_bank_file_shows_error(page: Page, tmp_path: Path):
    bad = tmp_path / "blogas.xml"
    bad.write_text("<ne-xml", encoding="utf-8")
    page.goto(f"{BASE}/apskaita#bank")
    page.locator('[data-upload="bank"]').set_input_files(str(bad))
    expect(page.locator("#toast.err")).to_contain_text("Nepavyko apdoroti", timeout=15_000)


def test_upload_wrong_extension_rejected(page: Page, tmp_path: Path):
    bad = tmp_path / "tabelis.txt"
    bad.write_text("x", encoding="utf-8")
    page.goto(f"{BASE}/apskaita#payroll")
    # accept=".xlsx" naršyklėje, bet serveris turi atmesti ir apeitą filtrą
    page.locator('[data-upload="payroll"]').set_input_files(str(bad))
    expect(page.locator("#toast.err")).to_contain_text(".xlsx", timeout=15_000)


def test_control_drafts_and_ai_bridge_roundtrip(page: Page):
    run_all(page)
    page.locator('.steps button[data-view="control"]').click()
    sec = page.locator("#view-control")
    expect(sec.locator("[data-drafts] details")).not_to_have_count(0, timeout=30_000)
    sec.locator("[data-drafts] summary").first.click()
    expect(sec.locator("[data-drafts] pre").first).to_be_visible()

    page.get_by_role("button", name="Paruošti tekstą Claude").click()
    expect(page.locator("#ai-guard .guard.ok")).to_be_visible()
    expect(page.get_by_role("button", name="Kopijuoti")).to_be_enabled()
    text = page.locator("#ai-text").inner_text()
    assert "@" not in text and not re.search(r"\b[1-6]\d{10}\b", text)
    token = re.search(r"\[DARB_\d{3}\]", text).group(0)

    page.locator("#ai-answer").fill(f"Sveiki, {token}, prašome patikslinti.")
    page.get_by_role("button", name="Atkurti tikrus duomenis").click()
    restored = page.locator("#ai-restored")
    expect(restored).to_contain_text("Sveiki")
    expect(restored).not_to_contain_text(token)


def test_restore_without_package_warns(page: Page):
    page.goto(f"{BASE}/apskaita#control")
    page.get_by_role("button", name="Atkurti tikrus duomenis").click()
    expect(page.locator("#toast.err")).to_contain_text("Pirma paruoškite")


@pytest.mark.parametrize("width", [375, 768, 1280])
def test_no_horizontal_overflow(page: Page, width: int):
    page.set_viewport_size({"width": width, "height": 900})
    for view in ("overview", "invoices", "payroll", "declarations", "control"):
        page.goto(f"{BASE}/apskaita#{view}")
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 1, f"{view} @ {width}px: horizontalus scroll {overflow}px"
