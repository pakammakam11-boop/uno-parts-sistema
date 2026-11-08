"""Sąskaitų modulio testai: PDF generavimas -> skaitymas -> patikros -> Rivilė XLSX."""
import shutil
from decimal import Decimal

import pdfplumber
import pytest
from openpyxl import load_workbook

from core import world
from core.invoices import (DocData, ParsedInvoice, Party, _parse_lines, draw_invoice, process_folder,
                           render_sales_invoice_pdf)
from core.models import money
from core.rivile_export import COLUMNS, purchases_to_xlsx, sales_to_xlsx
from data import gen_invoices

VAT_ERR = world.PURCHASE_INVOICES[world.PDF_VAT_ERROR_IDX]


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    out = tmp_path_factory.mktemp("saskaitos")
    gen_invoices.main(out)
    return out


@pytest.fixture(scope="module")
def processed(folder):
    return process_folder(folder)


def test_generates_60_pdfs(folder):
    assert len(list(folder.glob("*.pdf"))) == 60


def test_all_60_parse_with_correct_fields(processed):
    parsed, _ = processed
    assert len(parsed) == 60
    by_number = {p.number: p for p in parsed}
    for inv in world.PURCHASE_INVOICES:
        p = by_number[inv.number]
        assert p.supplier_code == inv.supplier_code
        assert p.supplier_name == world.supplier_by_code(inv.supplier_code).name
        assert p.issue_date == inv.issue_date and p.due_date == inv.due_date
        assert p.net == inv.net
        assert len(p.lines) == len(inv.lines)
        expected_gross = inv.gross + (Decimal("3.40") if inv is VAT_ERR else 0)
        assert p.gross == money(expected_gross)
        assert p.confidence == 1.0


def test_three_layouts_in_use(folder):
    texts = set()
    for pdf_path in sorted(folder.glob("*.pdf"))[:12]:
        with pdfplumber.open(str(pdf_path)) as pdf:
            first = pdf.pages[0].extract_text().splitlines()
        texts.add(first[0].split()[0])
    assert len(texts) >= 3


def test_vat_error_is_flagged_and_only_that(processed):
    _, findings = processed
    assert [f.ref for f in findings if f.severity == "error"] == [VAT_ERR.number]
    f = findings[0]
    assert "PVM" in f.title and f.suggested_action
    assert Decimal(f.extra["diff"]) == Decimal("3.40")
    assert not [x for x in findings if x.title.startswith(("Nežinomas", "Dublikatas", "Trūksta"))]


def test_duplicate_detected(folder, tmp_path):
    for pdf_path in list(folder.glob("*.pdf"))[:5]:
        shutil.copy(pdf_path, tmp_path / pdf_path.name)
    first = sorted(tmp_path.glob("*.pdf"))[0]
    shutil.copy(first, tmp_path / "kopija_zz.pdf")
    parsed, findings = process_folder(tmp_path)
    dups = [f for f in findings if f.title.startswith("Dublikatas")]
    assert len(parsed) == 6 and len(dups) == 1
    assert dups[0].ref == parsed[0].number


def test_unknown_supplier_flagged(tmp_path):
    inv = world.PURCHASE_INVOICES[0]
    c = world.COMPANY
    doc = DocData("XYZ-1", inv.issue_date, inv.due_date,
                  Party("UAB „Nežinomas“", "399999999", "LT399999991", "LT99 7300 0100 0000 9999"),
                  Party(c.name, c.code, c.vat_code, c.iban), inv.lines, inv.net, inv.vat, inv.gross)
    draw_invoice(tmp_path / "x.pdf", gen_invoices.LAYOUT_A, doc)
    parsed, findings = process_folder(tmp_path)
    assert parsed[0].supplier_code == ""
    assert any(f.title == "Nežinomas tiekėjas" and f.severity == "error" for f in findings)


def test_missing_fields_flagged(tmp_path):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(tmp_path / "tuscia.pdf"))
    c.drawString(50, 750, "Kažkoks dokumentas be rekvizitų")
    c.save()
    parsed, findings = process_folder(tmp_path)
    assert parsed[0].confidence < 0.5
    assert any(f.title.startswith("Trūksta") for f in findings)


def _rows(ws):
    heads = [c.value for c in ws[1]]
    return [dict(zip(heads, r)) for r in ws.iter_rows(min_row=2, values_only=True)]


def test_purchases_xlsx_structure(processed, tmp_path):
    parsed, _ = processed
    wb = load_workbook(purchases_to_xlsx(parsed, tmp_path / "pirkimai.xlsx"))
    assert wb.sheetnames == ["Clients", "Antrastes", "Purchase_invoice_lines", "Info"]
    clients = _rows(wb["Clients"])
    assert [c[COLUMNS["ref"]] for c in clients] == [f"REF{n:07d}" for n in range(1, len(clients) + 1)]
    assert len({c[COLUMNS["code"]] for c in clients}) == len(clients)
    assert all(c[COLUMNS["is_supplier"]] == 1 and c[COLUMNS["reg_code"]] for c in clients)
    assert len(_rows(wb["Antrastes"])) == 60
    line_heads = [c.value for c in wb["Purchase_invoice_lines"][1]]
    for name in ("DepartmentCode", "ItemCode", "Quantity", "UnitOfMeasureCode", "ItemName", "Price", "Notes"):
        assert name in line_heads
    assert line_heads[-1] == COLUMNS["doc_number"]
    info = " ".join(str(r[0]) for r in wb["Info"].iter_rows(values_only=True) if r[0])
    assert "tik EILUTES" in info and "Rivilė API" in info and "guide.rivile.cloud" in info


def test_purchases_xlsx_amounts_match_world(processed, tmp_path):
    parsed, _ = processed
    wb = load_workbook(purchases_to_xlsx(parsed, tmp_path / "p.xlsx"))
    by_number = {i.number: i for i in world.PURCHASE_INVOICES}
    headers = {h[COLUMNS["doc_number"]]: h for h in _rows(wb["Antrastes"])}
    sums: dict[str, Decimal] = {}
    for row in _rows(wb["Purchase_invoice_lines"]):
        number = row[COLUMNS["doc_number"]]
        sup = world.supplier_by_code(headers[number][COLUMNS["supplier_code"]])
        assert row[COLUMNS["item_code"]] == "S" + sup.expense_account
        assert row[COLUMNS["department"]] == "UNO" and row[COLUMNS["unit"]] == "VNT"
        assert row[COLUMNS["notes"]] == number
        line_net = money(Decimal(str(row[COLUMNS["quantity"]])) * Decimal(str(row[COLUMNS["price"]])))
        sums[number] = sums.get(number, Decimal(0)) + line_net
    for number, h in headers.items():
        inv = by_number[number]
        assert h[COLUMNS["vat_code"]] == "PVM1"
        assert sums[number] == inv.net == money(h[COLUMNS["amount_net"]])
        assert money(h[COLUMNS["amount_vat"]]) == inv.vat   # eilučių PVM teisingas ir klaidingam PDF
        assert h[COLUMNS["supplier_code"]] == inv.supplier_code


def test_vat_code_mapping():
    from core.rivile_export import vat_code
    rates = ("0.21", "0.09", "0.00", "0.12")
    assert [vat_code(Decimal(r)) for r in rates] == ["PVM1", "PVM2", "PVM5", "PVM58"]


def _parsed_with(number, desc):
    from core.models import InvoiceLine
    sup = world.SUPPLIERS[0]
    return ParsedInvoice(number=number, supplier_name=sup.name, supplier_code=sup.rivile_code,
                         supplier_company_code=sup.company_code, supplier_vat_code=sup.vat_code,
                         lines=[InvoiceLine(desc, Decimal(1), Decimal(100), Decimal("0.21"))])


def test_xlsx_text_never_becomes_formula(tmp_path):
    import zipfile
    parsed = [_parsed_with("=1+1", "=SUM(A1)"), _parsed_with("+2", "@cmd")]
    path = purchases_to_xlsx(parsed, tmp_path / "f.xlsx")
    wb = load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (ws.title, cell.coordinate)
    lines = _rows(wb["Purchase_invoice_lines"])
    assert lines[0][COLUMNS["item_name"]] == "=SUM(A1)" and lines[0][COLUMNS["doc_number"]] == "=1+1"
    with zipfile.ZipFile(path) as z:
        assert not any("<f>" in z.read(n).decode("utf-8", "ignore")
                       for n in z.namelist() if n.startswith("xl/worksheets/"))


def test_line_total_mismatch_flagged():
    rows = ["Paslauga 1,00 100,00 21 % 200,00 €", "Kita 2,00 50,00 21 % 100,00 €"]
    lines, mismatches = _parse_lines(rows)
    assert len(lines) == 2 and len(mismatches) == 1
    assert mismatches[0] == ("Paslauga", Decimal("200.00"), Decimal("100.00"))
    from core.invoices import _check_line_totals
    p = ParsedInvoice(number="X-1", line_mismatches=mismatches)
    f = _check_line_totals(p)[0]
    assert f.severity == "error" and f.title == "Eilutės suma neatitinka" and f.suggested_action


def test_sales_pdf_and_xlsx(tmp_path):
    pdf_path = tmp_path / "p.pdf"
    inv = world.SALES_INVOICES[0]
    render_sales_invoice_pdf(inv, pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        text = pdf.pages[0].extract_text()
    assert inv.number in text and inv.buyer_name in text
    wb = load_workbook(sales_to_xlsx(world.SALES_INVOICES, tmp_path / "s.xlsx"))
    assert wb.sheetnames == ["Clients", "Antrastes", "Sales_invoice_lines", "Info"]
    assert all(r[COLUMNS["is_supplier"]] == 0 for r in _rows(wb["Clients"]))
    total = sum(money(r[COLUMNS["amount_net"]]) for r in _rows(wb["Antrastes"]))
    assert total == money(sum(i.net for i in world.SALES_INVOICES))


# ---- S4 (saugumo auditas): PDF dekompresijos bomba ----

def _bomb_pdf(path, chars=3_000_000):
    """Mažas PDF (~KB), kurio FlateDecode turinio srautas išsipakuoja į daugiau nei 2 MB teksto."""
    import zlib
    content = b"BT /F1 12 Tf 10 10 Td (" + b"A" * chars + b") Tj ET"
    comp = zlib.compress(content, 9)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


def test_decompression_bomb_becomes_finding_not_crash(tmp_path):
    from core import invoices, pdf_safe
    bomb = tmp_path / "bomba.pdf"
    _bomb_pdf(bomb)
    assert bomb.stat().st_size < 20_000
    with pytest.raises(pdf_safe.PdfUnsafe):
        pdf_safe.extract_text(bomb)
    parsed, findings = invoices.process_folder(tmp_path)
    assert parsed == [] and [f.title for f in findings] == ["PDF per didelis arba sugadintas"]


def test_pdf_timeout_is_unsafe(tmp_path, monkeypatch):
    from core import pdf_safe
    bomb = tmp_path / "bomba.pdf"
    _bomb_pdf(bomb)
    monkeypatch.setattr(pdf_safe, "TIMEOUT_SECONDS", 0.01)
    with pytest.raises(pdf_safe.PdfUnsafe):
        pdf_safe.extract_text(bomb)


def test_pdf_page_limit(tmp_path, monkeypatch):
    from core import pdf_safe
    bomb = tmp_path / "x.pdf"
    _bomb_pdf(bomb, chars=10)
    monkeypatch.setattr(pdf_safe, "MAX_PAGES", 0)
    with pytest.raises(pdf_safe.PdfUnsafe):
        pdf_safe.extract_text(bomb)
    monkeypatch.setattr(pdf_safe, "MAX_PAGES", 50)
    assert pdf_safe.extract_text(bomb).startswith("AAAA")


def test_pdf_parse_concurrency_is_bounded():
    """Sol S7 r2: vienu metu ne daugiau 2 PDF vaikų procesų."""
    from core import invoices
    assert invoices.PARSE_SLOTS._initial_value == 2


def test_pdf_child_stderr_is_discarded(monkeypatch):
    """Sol S6 r2: vaiko stderr nekaupiamas tėviniame procese."""
    import subprocess
    from core import pdf_safe
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"tekstas")
    monkeypatch.setattr(pdf_safe.subprocess, "run", fake_run)
    assert pdf_safe.extract_text("x.pdf") == "tekstas"
    assert seen["stderr"] is subprocess.DEVNULL and "capture_output" not in seen
