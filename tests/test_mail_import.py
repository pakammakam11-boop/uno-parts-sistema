"""Pašto importo testai: imaplib užmokintas, tinklo nėra."""
import os
from email.message import EmailMessage
from pathlib import Path

import pytest

from app import services
from core import invoice_ai, invoices, mail_import
from core.invoices import ParsedInvoice
from core.mail_import import MailError, MailNotConfigured

SECRET = "slaptas-app-pass-123"


def _message(attachments: list[tuple[str, str, bytes]]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "tiekejas@example.lt"
    msg["Subject"] = "Saskaita"
    msg["Date"] = "Wed, 04 Nov 2026 10:00:00 +0200"
    msg.set_content("Prisegta sąskaita")
    for name, ctype, data in attachments:
        main, sub = ctype.split("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


class FakeIMAP:
    messages: dict[bytes, bytes] = {}
    selected: tuple | None = None

    def __init__(self, host, port, timeout=None):
        self.timeout = timeout

    def login(self, user, password):
        if password != SECRET:
            raise mail_import.imaplib.IMAP4.error(f"LOGIN failed {password}")

    def select(self, mailbox, readonly=False):
        FakeIMAP.selected = (mailbox, readonly)
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, uid, spec):
        assert "PEEK" in spec  # nežymi perskaitytu
        return "OK", [(b"1 (BODY[] {1}", self.messages[uid]), b")"]

    def logout(self):
        return "BYE", []


@pytest.fixture
def imap(monkeypatch):
    monkeypatch.setenv("UNO_IMAP_USER", "a@gmail.com")
    monkeypatch.setenv("UNO_IMAP_PASSWORD", SECRET)
    monkeypatch.setattr(mail_import.imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.messages = {}
    return FakeIMAP


def test_saves_pdfs_skips_other(imap, tmp_path):
    imap.messages = {b"1": _message([("../../evil/Saskaita 1.PDF", "application/pdf", b"%PDF-1"),
                                     ("foto.png", "image/png", b"png")])}
    result = mail_import.fetch_invoice_pdfs(tmp_path)
    assert len(result) == 1
    saved = tmp_path / result[0]["file"]
    assert saved.read_bytes() == b"%PDF-1" and saved.parent == tmp_path
    assert result[0]["file"].startswith("1_") and ".." not in result[0]["file"]
    assert result[0]["sender"] == "tiekejas@example.lt" and result[0]["date"] == "2026-11-04"
    assert imap.selected == ("INBOX", True)


def test_pdf_by_content_type_and_newest_first(imap, tmp_path):
    imap.messages = {b"1": _message([("a.bin", "application/pdf", b"x")]),
                     b"2": _message([("b.pdf", "application/octet-stream", b"y")])}
    result = mail_import.fetch_invoice_pdfs(tmp_path)
    assert [r["file"][0] for r in result] == ["2", "1"]


def test_oversized_attachment_skipped(imap, tmp_path, monkeypatch):
    monkeypatch.setattr(mail_import, "MAX_ATTACHMENT_BYTES", 3)
    imap.messages = {b"1": _message([("a.pdf", "application/pdf", b"12345")])}
    assert mail_import.fetch_invoice_pdfs(tmp_path) == []


def test_safe_filename_strips_separators_and_caps_length():
    name = mail_import.safe_filename("..\\..\\a/b\\" + "x" * 300 + ".pdf", "7")
    assert "/" not in name and "\\" not in name and len(name) < 100 and name.startswith("7_")


def test_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("UNO_IMAP_USER", raising=False)
    monkeypatch.delenv("UNO_IMAP_PASSWORD", raising=False)
    monkeypatch.setattr(mail_import, "_env", lambda name: "")  # be Windows registro atsarginio kelio
    with pytest.raises(MailNotConfigured):
        mail_import.fetch_invoice_pdfs(tmp_path)


def test_login_error_hides_password(imap, tmp_path, monkeypatch):
    monkeypatch.setenv("UNO_IMAP_PASSWORD", "blogas-slaptazodis")
    with pytest.raises(MailError) as exc:
        mail_import.fetch_invoice_pdfs(tmp_path)
    assert "blogas-slaptazodis" not in str(exc.value) and exc.value.__cause__ is None


# ---------------- invoice_ai

def test_enrich_fills_only_missing(monkeypatch):
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: {
        "number": "AI-1", "supplier_name": "AI Tiekėjas", "issue_date": "2026-10-05",
        "net": "100.00", "vat": "21.00", "gross": "121.00",
        "lines": [{"description": "Paslauga", "qty": "2", "unit_price": "50.00", "vat_rate": 21, "net": "100.00"}]})
    p = ParsedInvoice(number="REGEX-9")
    out = invoice_ai.enrich(p, "tekstas")
    assert out.number == "REGEX-9"  # regex nepaliesta
    assert out.supplier_name == "AI Tiekėjas" and out.issue_date.isoformat() == "2026-10-05"
    assert str(out.gross) == "121.00" and len(out.lines) == 1 and str(out.lines[0].vat_rate) == "0.21"


def test_enrich_skips_lines_with_bad_numbers(monkeypatch):
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: {
        "lines": [{"description": "X", "qty": "abc", "unit_price": "1", "vat_rate": 21}], "net": "bad"})
    out = invoice_ai.enrich(ParsedInvoice(), "t")
    assert out.lines == [] and out.net is None


def test_enrich_without_ai_result(monkeypatch):
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: None)
    p = ParsedInvoice(number="1")
    assert invoice_ai.enrich(p, "t") is p


def test_parse_json_strips_fences():
    assert invoice_ai._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert invoice_ai._parse_json("nesamone") is None


# ---------------- services

@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """Testai nepriklauso nuo šio PC naudotojo kintamųjų (setup_mail.ps1)."""
    real = mail_import._env
    monkeypatch.setattr(mail_import, "_env", lambda name: os.environ.get(name, "") if name.startswith("UNO_MAIL") else real(name))


def test_run_invoices_mail_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(services, "ROOT", tmp_path)
    monkeypatch.setattr(services, "OUT", tmp_path / "out")
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", lambda dest, **kw: [])
    result = services.run_invoices_mail()
    assert result["kpis"][0] == {"label": "Iš pašto", "value": "0 PDF"}
    assert any(f["title"] == "Pašte naujų sąskaitų nerasta" for f in result["findings"])


def test_run_invoices_mail_processes_downloaded(monkeypatch, tmp_path):
    from data import gen_invoices

    monkeypatch.setattr(services, "ROOT", tmp_path)
    monkeypatch.setattr(services, "OUT", tmp_path / "out")
    seen = {}

    def fake_fetch(dest: Path, **kw):
        gen_invoices.main(dest)
        seen["dest"] = dest
        return [{"file": "x.pdf"}] * 2

    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", fake_fetch)
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: None)
    monkeypatch.setattr(invoices, "is_for_company", lambda text: True)   # demo PDF be UNO kodo
    result = services.run_invoices_mail()
    assert list((tmp_path / "data" / "uploads" / "mail").glob("*.pdf"))
    assert result["kpis"][0]["value"] == "2 PDF"
    assert result["table"]["rows"]


def test_mail_skips_other_companies_invoices(monkeypatch, tmp_path):
    from data import gen_invoices

    monkeypatch.setattr(services, "ROOT", tmp_path)
    monkeypatch.setattr(services, "OUT", tmp_path / "out")
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", lambda dest, **kw: gen_invoices.main(dest) or [])
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: None)
    result = services.run_invoices_mail()
    assert not result["table"]["rows"]
    assert any(f["title"] == invoices.MSG_NOT_COMPANY for f in result["findings"])


def test_is_for_company_by_code_or_name():
    assert invoices.is_for_company("Pirkėjas: X, įm. kodas 305 953 678")
    assert invoices.is_for_company("Pirkėjas UAB UNO PARTS")
    assert not invoices.is_for_company("Pirkėjas MB Deluks, kodas 306313318")


# ---- Sol 1 rato taisymai ----

def test_same_name_attachments_do_not_overwrite(imap, tmp_path):
    imap.messages = {b"5": _message([("s.pdf", "application/pdf", b"%PDF-1"), ("s.pdf", "application/pdf", b"%PDF-2")])}
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert len(got) == 2 and len(list(tmp_path.glob("*.pdf"))) == 2


def test_pdf_content_type_with_other_extension_saved_as_pdf(imap, tmp_path):
    imap.messages = {b"6": _message([("a.bin", "application/pdf", b"%PDF-1")])}
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert got and got[0]["file"].endswith(".pdf") and (tmp_path / got[0]["file"]).exists()


def test_fetch_failure_is_error_not_empty_import(imap, tmp_path, monkeypatch):
    imap.messages = {b"7": _message([("s.pdf", "application/pdf", b"%PDF-1")])}
    monkeypatch.setattr(FakeIMAP, "fetch", lambda self, uid, spec: ("NO", [None]))
    with pytest.raises(MailError):
        mail_import.fetch_invoice_pdfs(tmp_path)


def test_ai_rejects_nan_and_flags_line_mismatch(monkeypatch):
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: {
        "net": "NaN", "vat": "Infinity",
        "lines": [{"description": "Detalė", "qty": "2", "unit_price": "10.00", "vat_rate": 21, "net": "25.00"}]})
    p = invoice_ai.enrich(ParsedInvoice(source_file="x.pdf"), "tekstas")
    assert p.net is None and p.vat is None
    assert p.lines and p.line_mismatches and p.line_mismatches[0][1] == invoice_ai.Decimal("25.00")


def test_declarations_wait_for_uncomputed_sources():
    from core import control
    rows = control.declarations({})["table"]["rows"]
    assert all(r["status"].startswith("Laukia") for r in rows)


def test_real_mailbox_goes_to_custom_dir_without_ai(monkeypatch, tmp_path):
    from data import gen_invoices

    real_dir = tmp_path / "UNO_DATA" / "mail"
    monkeypatch.setenv("UNO_MAIL_DIR", str(real_dir))
    monkeypatch.setenv("UNO_MAIL_AI", "0")
    monkeypatch.setattr(services, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(services, "OUT", tmp_path / "out")
    seen = {}
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", lambda dest, **kw: (seen.setdefault("dest", dest), gen_invoices.main(dest), [{"file": "x.pdf"}])[2])
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: pytest.fail("AI neturi būti kviečiamas"))
    services.run_invoices_mail()
    assert list(real_dir.glob("*.pdf")) and not (tmp_path / "repo" / "data").exists()


def test_ai_cache_skips_second_call(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(invoice_ai, "_call_claude", lambda text: calls.append(text) or {"number": "A-1"})
    assert invoice_ai.extract_fields("tekstas", tmp_path) == {"number": "A-1"}
    assert invoice_ai.extract_fields("tekstas", tmp_path) == {"number": "A-1"}
    assert len(calls) == 1


def test_prefer_ai_overrides_regex_guess(monkeypatch):
    monkeypatch.setattr(invoice_ai, "extract_fields", lambda text: {"supplier_name": "MB Tikras"})
    p = invoice_ai.enrich(ParsedInvoice(supplier_name="Pirkėjas"), "t", prefer_ai=True)
    assert p.supplier_name == "MB Tikras"


# ---- Tiekėjo sukūrimas iš radinio ----

def test_create_supplier_then_invoice_matches(monkeypatch, tmp_path):
    from core import invoices, suppliers

    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "suppliers.json"))
    monkeypatch.setattr(mail_import, "_env", lambda name: os.environ.get(name, ""))
    p = ParsedInvoice(supplier_name="MB Tikras", supplier_company_code="305842101")
    found = invoices._check_supplier(p)
    assert found and found[0].extra["create_supplier"]["company_code"] == "305842101"
    sup, created = suppliers.add("MB Tikras", "305842101")
    again, created2 = suppliers.add("MB Tikras", "305842101")
    assert created and not created2 and sup == again and sup.rivile_code == "TIEK101"
    assert invoices._match_supplier(p) == "TIEK101"
    assert suppliers.add("Kitas", vat_code="LT1")[0].rivile_code == "TIEK102"


def test_supplier_without_codes_matches_by_name(monkeypatch, tmp_path):
    from core import invoices, suppliers

    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(mail_import, "_env", lambda name: os.environ.get(name, ""))
    suppliers.add("Meta Platforms Ireland Limited")
    assert invoices._match_supplier(ParsedInvoice(supplier_name="META Platforms Ireland Limited")) == "TIEK101"
    with pytest.raises(ValueError):
        suppliers.add("  ")


def test_api_create_supplier_reruns_invoices(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(mail_import, "_env", lambda name: os.environ.get(name, ""))
    calls = []
    main.RESULTS["invoices"] = {"kpis": [], "counts": {"error": 0, "warning": 0}, "_findings": [],
                                "_source": ("f", False, None, [])}
    monkeypatch.setattr(services, "rerun_invoices", lambda r: calls.append(r) or {**r, "kpis": [{"label": "x", "value": "1"}]})
    client = TestClient(main.app)
    r = client.post("/api/suppliers", json={"name": "MB Naujas", "company_code": "300000001"})
    assert r.status_code == 200 and r.json()["supplier"]["code"] == "TIEK101" and calls
    assert client.post("/api/suppliers", json={"name": ""}).status_code == 422
    main.RESULTS.clear()


def test_failed_login_keeps_previous_pdfs(monkeypatch, tmp_path):
    folder = tmp_path / "data" / "uploads" / "mail"
    folder.mkdir(parents=True)
    (folder / "senas.pdf").write_bytes(b"%PDF-1")
    monkeypatch.setattr(services, "ROOT", tmp_path)

    def boom(dest, **kw):
        raise MailError("nepavyko")

    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", boom)
    with pytest.raises(MailError):
        services.run_invoices_mail()
    assert (folder / "senas.pdf").exists() and not (tmp_path / "data" / "uploads" / "mail_incoming").exists()


def test_gmail_searches_only_pdf_mails(imap, tmp_path, monkeypatch):
    real_env = mail_import._env  # Windows naudotojo kintamieji neturi įtakoti testo
    monkeypatch.setattr(mail_import, "_env", lambda n: "" if n == "UNO_IMAP_QUERY" else real_env(n))
    seen = []
    monkeypatch.setattr(imap, "search", lambda self, charset, *c: (seen.append(c), ("OK", [b""]))[1])
    mail_import.fetch_invoice_pdfs(tmp_path, days=30)
    assert seen[0] == ("X-GM-RAW", '"has:attachment filename:pdf newer_than:30d"')


def test_other_imap_host_uses_since(imap, tmp_path, monkeypatch):
    monkeypatch.setenv("UNO_IMAP_HOST", "imap.example.lt")
    seen = []
    monkeypatch.setattr(imap, "search", lambda self, charset, *c: (seen.append(c), ("OK", [b""]))[1])
    mail_import.fetch_invoice_pdfs(tmp_path, days=30)
    assert seen[0][0] == "SINCE"


def test_gmail_extra_query_appended(imap, tmp_path, monkeypatch):
    monkeypatch.setenv("UNO_IMAP_QUERY", '(saskaita OR invoice) -from:me "x"')
    seen = []
    monkeypatch.setattr(imap, "search", lambda self, charset, *c: (seen.append(c), ("OK", [b""]))[1])
    mail_import.fetch_invoice_pdfs(tmp_path, days=30)
    assert seen[0] == ("X-GM-RAW", '"has:attachment filename:pdf newer_than:30d (saskaita OR invoice) -from:me x"')
