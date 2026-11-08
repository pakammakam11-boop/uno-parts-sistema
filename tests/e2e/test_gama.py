"""/app GAMA patikra naršyklėje. Snapshot komanda pakeista netikru skriptu (sintetiniai duomenys, tmp katalogas).

Ekrano nuotraukos: jei nustatytas UNO_E2E_SHOTS (katalogas už repo), įrašomos ten.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from core.gama import load_snapshot
from core.gama_check import check_month
from tests.e2e.test_prod import assert_no_overflow, goto_step, open_app, page, server, set_period  # noqa: F401 (fixtures)

FAKE = Path(__file__).resolve().parent / "fake_gama_snapshot.py"
MONTH = "2026-09"
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gama_snapshot_synthetic.json"
_, SUMMARY = check_month(load_snapshot(FIXTURE), MONTH)
N_FINDINGS = sum(SUMMARY["findings"].values())


@pytest.fixture()
def gama_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UNO_GAMA_SNAPSHOT_CMD", json.dumps([sys.executable, str(FAKE)]))
    monkeypatch.setenv("UNO_GAMA_DATA_DIR", str(tmp_path / "UNO_DATA"))
    monkeypatch.setenv("UNO_GAMA_COMPANIES", "1")
    return tmp_path / "UNO_DATA"


def shot(page: Page, name: str) -> None:
    target = os.environ.get("UNO_E2E_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def run_gama(page: Page, server: str, month: str = MONTH) -> None:
    open_app(page, server, period=month)
    goto_step(page, "gama")
    expect(page.locator("#gama-month")).to_have_value(month)
    expect(page.locator("#gama-refresh")).to_be_checked()
    page.get_by_role("button", name="Tikrinti mėnesį GAMA").click()


@pytest.mark.parametrize("width", [1280, 375])
def test_gama_check_shows_findings_table_and_xlsx(page: Page, server: str, gama_env, width: int, tmp_path):
    page.set_viewport_size({"width": width, "height": 900})
    expect_empty = page.locator("#gama-runs .empty")
    open_app(page, server, period=MONTH)
    goto_step(page, "gama")
    expect(expect_empty).to_contain_text("patikrų dar nėra")
    page.get_by_role("button", name="Tikrinti mėnesį GAMA").click()

    expect(page.locator("#gama-result")).to_be_visible()
    expect(page.locator("#gama-result-title")).to_be_focused()
    expect(page.locator("#gama-components .total")).to_have_count(len(SUMMARY["components"]))
    expect(page.locator("#gama-components")).to_contain_text("neatitikimų 1")
    rows = page.locator("#gama-findings tbody tr")
    expect(rows).to_have_count(N_FINDINGS)
    expect(rows.first).to_have_attribute("data-sev", "error")       # klaidos pirmos
    expect(rows.last).to_have_attribute("data-sev", "info")
    psd = rows.filter(has_text="PSD: neatitikimas")
    expect(psd).to_contain_text("00005")
    expect(psd.locator("td").last).to_have_text("−0,45 €")  # lt-LT minusas
    expect(page.locator("#gama-runs tbody tr")).to_have_count(1)
    expect(page.locator("#gama-runs [aria-current]")).to_have_text("Rodoma")
    expect(page.locator('[data-badge="gama"]')).to_have_text(str(SUMMARY["findings"]["error"] + SUMMARY["findings"]["warning"]))

    page.locator('#gama-findings [data-filter="warning"]').click()
    expect(page.locator("#gama-findings tbody tr:visible")).to_have_count(SUMMARY["findings"]["warning"])
    page.locator('#gama-findings [data-filter="all"]').click()
    expect(page.locator("#gama-findings tbody tr:visible")).to_have_count(N_FINDINGS)

    assert_no_overflow(page, f"gama {width}")
    shot(page, f"gama_{width}")

    with page.expect_download() as dl:
        page.get_by_role("button", name="Atsisiųsti XLSX").click()
    download = dl.value
    assert download.suggested_filename.startswith("gama_check_2026-09")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    assert saved.read_bytes()[:2] == b"PK"


def test_gama_restores_last_run_after_reload(page: Page, server: str, gama_env):
    run_gama(page, server)
    expect(page.locator("#gama-findings tbody tr")).to_have_count(N_FINDINGS)
    page.reload()
    set_period(page, MONTH)
    goto_step(page, "gama")
    expect(page.locator("#gama-findings tbody tr")).to_have_count(N_FINDINGS)
    page.locator("#gama-refresh").uncheck()                 # be atnaujinimo: naudojamas jau parsiųstas failas
    page.get_by_role("button", name="Tikrinti mėnesį GAMA").click()
    expect(page.locator("#gama-runs tbody tr")).to_have_count(2)


def test_gama_snapshot_failure_shows_error(page: Page, server: str, gama_env):
    run_gama(page, server, month="2026-07")
    err = page.locator("#gama-error")
    expect(err).to_be_visible()
    expect(err).to_have_text("GAMA eksportas nepavyko: Nepavyko prisijungti prie GAMA serverio")
    expect(page.locator("#gama-result")).to_be_hidden()
    expect(page.locator("#gama-run")).to_be_enabled()
    shot(page, "gama_error")


def test_gama_previous_runs_ignore_out_of_order_responses(page: Page, server: str, gama_env):
    run_gama(page, server)
    expect(page.locator("#gama-findings tbody tr")).to_have_count(N_FINDINGS)
    page.locator("#gama-refresh").uncheck()
    page.get_by_role("button", name="Tikrinti mėnesį GAMA").click()
    rows = page.locator("#gama-runs tbody tr")
    expect(rows).to_have_count(2)
    newer, older = (int(rows.nth(i).get_attribute("data-run")) for i in (0, 1))
    assert newer != older
    # senesnės patikros atsakymas vėluoja ir grįžta po naujesnės patikros atsakymo
    page.evaluate("""(id) => { const orig = window.fetch.bind(window);
        window.fetch = (u, o) => { const p = orig(u, o);
          return String(u).endsWith('/runs/' + id) ? p.then((r) => new Promise((res) => setTimeout(() => res(r), 900))) : p; }; }""",
                  older)
    page.locator(f'[data-open-run="{older}"]').click()
    page.locator(f'[data-open-run="{newer}"]').click()
    expect(page.locator(f'tr[data-run="{newer}"] [aria-current]')).to_have_text("Rodoma")
    page.wait_for_timeout(1500)
    expect(page.locator(f'tr[data-run="{newer}"] [aria-current]')).to_have_text("Rodoma")
    expect(page.locator(f'tr[data-run="{older}"] [aria-current]')).to_have_count(0)


def test_gama_runs_list_ignores_stale_list_response(page: Page, server: str, gama_env):
    run_gama(page, server)
    expect(page.locator("#gama-runs tbody tr")).to_have_count(1)
    # pirmas /runs?month atsakymas vėluoja ir grįžta tuščias po antro (šviežio) atsakymo
    page.evaluate("""() => { const orig = window.fetch.bind(window); let n = 0;
        window.fetch = (u, o) => String(u).includes('/gama/runs?month') && n++ === 0
          ? new Promise((res) => setTimeout(() => res(new Response('{"runs":[]}',
              { status: 200, headers: { 'Content-Type': 'application/json' } })), 900))
          : orig(u, o); }""")
    page.evaluate("() => { loadGamaRuns(false); loadGamaRuns(false); }")
    expect(page.locator("#gama-runs tbody tr")).to_have_count(1)
    page.wait_for_timeout(1500)
    expect(page.locator("#gama-runs tbody tr")).to_have_count(1)
