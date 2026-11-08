"""Galas-į-galą per API: visi moduliai, kontrolė, AI tiltas, DU skaičiuoklė."""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import RESULTS, app
from core.world import EMPLOYEES


@pytest.fixture(scope="module")
def client():
    RESULTS.clear()
    return TestClient(app)


def test_index_and_static(client):
    assert client.get("/apskaita").status_code == 200
    assert client.get("/static/app.js").status_code == 200


@pytest.mark.parametrize("module", ["invoices", "bank", "payroll"])
def test_run_module(client, module):
    r = client.post(f"/api/run/{module}")
    assert r.status_code == 200
    j = r.json()
    assert j["kpis"] and j["table"]["rows"] and j["downloads"]
    for d in j["downloads"]:
        assert client.get(d["url"]).status_code == 200


def test_control_finds_planted_issues(client):
    j = client.get("/api/control").json()
    titles = " ".join(f["title"] for f in j["findings"])
    assert "Dvigubas apmokėjimas" in titles
    assert "Trūksta sąskaitos" in titles
    assert j["drafts"]


def test_ai_package_has_no_personal_data(client):
    j = client.post("/api/ai/package").json()
    assert j["safe_to_send"]
    text = j["text"]
    for e in EMPLOYEES:
        assert e.full_name not in text and e.personal_code not in text and e.tab_no not in text
    # sąskaitų numeriai lieka skaitomi
    assert re.search(r"\b\d{3}-\d{9}\b", text)
    token = re.search(r"\[DARB_\d{3}\]", text).group(0)
    restored = client.post("/api/ai/restore", data={"session": j["session"], "text": f"Sveiki, {token}"}).json()["text"]
    assert token not in restored


def test_payroll_calc_roundtrip(client):
    j = client.post("/api/payroll/calc", data={"net": "846.96", "fixed_term": "0"}).json()
    values = {l["label"]: l["value"] for l in j["lines"]}
    assert values["Į rankas"] == "846,96 €"


def test_download_path_traversal_blocked(client):
    assert client.get("/api/download/..%2F..%2Fcore%2Fworld.py").status_code == 404


def test_error_invoices_held_from_export_s2():
    from app import services
    from core.world import PDF_VAT_ERROR_IDX, PURCHASE_INVOICES
    from openpyxl import load_workbook

    services.run_invoices()
    wb = load_workbook(services.OUT / "rivile_pirkimai_2026-10.xlsx")
    numbers = {str(c.value) for c in wb["Antrastes"]["A"]}
    assert PURCHASE_INVOICES[PDF_VAT_ERROR_IDX].number not in numbers


def test_gama_eip_download_listed_and_holds_error_invoice():
    from app import services
    from core.world import PDF_VAT_ERROR_IDX, PURCHASE_INVOICES
    from lxml import etree

    result = services.run_invoices()
    assert any(d["url"].endswith(".eip") for d in result["downloads"])
    raw = (services.OUT / "rivile_gama_pirkimai_2026-10.eip").read_bytes().decode("windows-1257")
    root = etree.fromstring(("<EIP>" + raw + "</EIP>").encode("utf-8"))   # Rivilės EIP be šaknies
    numbers = {e.text for e in root.iter("I06_DOK_NR")}
    assert numbers and PURCHASE_INVOICES[PDF_VAT_ERROR_IDX].number not in numbers


def test_partial_remaining_in_payment_file_s3():
    from app import services
    from core.world import PARTIAL_PAID_IDX, PURCHASE_INVOICES

    services.run_bank()
    xml = (services.OUT / "mokejimai_pain001.xml").read_text(encoding="utf-8")
    assert PURCHASE_INVOICES[PARTIAL_PAID_IDX].number in xml


def test_upload_corrupt_xml_is_422_s18(client):
    r = client.post("/api/upload/bank", files={"files": ("x.xml", b"<not-xml", "text/xml")})
    assert r.status_code == 422


def test_upload_rejects_wrong_type(client):
    r = client.post("/api/upload/bank", files={"files": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_declarations_page_lists_four_with_deadlines(client):
    j = client.get("/api/declarations").json()
    codes = [r["code"] for r in j["table"]["rows"]]
    assert codes == ["GPM313", "SAM", "i.SAF", "FR0600"]
    assert all(r["due"].startswith("iki ") for r in j["table"]["rows"])
    assert "declarations" in j["badges"]
    gpm = next(r for r in j["table"]["rows"] if r["code"] == "GPM313")
    assert "GPM" in gpm["detail"]


def test_control_no_longer_holds_declarations(client):
    j = client.get("/api/control").json()
    assert not any(f["ref"] in ("GPM313", "SAM", "FR0600") for f in j["findings"])


def test_badges_report_errors_and_warnings_separately(client):
    b = client.get("/api/overview").json()["badges"]
    assert set(b["bank"]) == {"error", "warning"}
    assert b["bank"]["warning"] > 0
