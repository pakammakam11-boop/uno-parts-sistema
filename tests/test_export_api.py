"""Mašininio EIP eksporto testai: raktas, tik naujos sąskaitos, patvirtinimas."""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import export_api, services
from app.main import app
from core.invoices import ParsedInvoice
from core.models import InvoiceLine

TOKEN = "testo-raktas-123"


def inv(number, code="K0001"):
    return ParsedInvoice(number=number, issue_date=date(2026, 10, 5), due_date=date(2026, 11, 4),
                         supplier_name="x", supplier_code=code, source_file="a/b.pdf",
                         lines=[InvoiceLine("Prekė", Decimal("1"), Decimal("10"), Decimal("0.21"))])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv(export_api.TOKEN_ENV, TOKEN)
    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "suppliers.json"))
    (tmp_path / "suppliers.json").write_text(json.dumps([
        {"rivile_code": "K0001", "name": "A", "company_code": "1", "vat_code": "", "iban": "", "email": "",
         "expense_account": "63027"}]), encoding="utf-8")
    state = {"export": [inv("SF-1"), inv("SF-2")]}
    monkeypatch.setattr(services, "run_invoices_mail", lambda: {"_export": state["export"]})
    c = TestClient(app)
    c.state = state
    return c


H = {export_api.HEADER: TOKEN}


def test_requires_token(client):
    assert client.get("/api/export/gama-eip").status_code == 401
    assert client.get("/api/export/gama-eip", headers={export_api.HEADER: "blogas"}).status_code == 401


def test_unacked_batch_repeats_then_ack_stops_duplicates(client):
    first = client.get("/api/export/gama-eip", headers=H)
    assert first.status_code == 200 and first.headers["X-UNO-Count"] == "2"
    assert b"SF-1" in first.content and b"SF-2" in first.content
    again = client.get("/api/export/gama-eip", headers=H)          # nepatvirtinta: ta pati siunta
    assert again.headers["X-UNO-Batch"] == first.headers["X-UNO-Batch"]
    ack = client.post(f"/api/export/gama-eip/ack?batch={first.headers['X-UNO-Batch']}", headers=H)
    assert ack.json() == {"acked": 2}
    assert client.get("/api/export/gama-eip", headers=H).status_code == 204


def test_only_new_invoices_after_ack(client):
    first = client.get("/api/export/gama-eip", headers=H)
    client.post(f"/api/export/gama-eip/ack?batch={first.headers['X-UNO-Batch']}", headers=H)
    client.state["export"] = [inv("SF-1"), inv("SF-2"), inv("SF-3")]
    new = client.get("/api/export/gama-eip", headers=H)
    assert new.headers["X-UNO-Count"] == "1" and b"SF-3" in new.content and b"SF-1" not in new.content


def test_bad_batch_rejected(client):
    assert client.post("/api/export/gama-eip/ack?batch=../x", headers=H).status_code == 400


def test_last_ping_shows_get_and_ack(client):
    assert export_api.last_ping()["last_at"] is None
    first = client.get("/api/export/gama-eip", headers=H)
    s = export_api.last_ping()
    assert s["last_kind"] == "get" and s["last_count"] == 2 and s["last_ack_at"] is None
    client.post(f"/api/export/gama-eip/ack?batch={first.headers['X-UNO-Batch']}", headers=H)
    s = export_api.last_ping()
    assert s["last_kind"] == "ack" and s["last_ack_count"] == 2 and s["acked_total"] == 2


def test_zip_has_eip_and_pdfs(client, tmp_path):
    import io
    import zipfile

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    one, two = inv("SF/1"), inv("SF-2")
    one.source_file = str(pdf)
    two.source_file = str(tmp_path / "nera.pdf")
    client.state["export"] = [one, two]
    r = client.get("/api/export/gama-eip?format=zip", headers=H)
    assert r.status_code == 200 and r.headers["X-UNO-Count"] == "2"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["uno.eip", "SF_1.pdf"]
