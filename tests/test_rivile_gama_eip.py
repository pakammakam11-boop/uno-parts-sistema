"""Rivilė GAMA EIP pirkimų importo failo testai."""
import json
import re
from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from core.invoices import ParsedInvoice
from core.models import InvoiceLine, money
from core.rivile_gama_eip import DEFAULT_REFS, build_eip, load_refs, purchases_to_eip


@pytest.fixture(autouse=True)
def _suppliers_file(tmp_path, monkeypatch):
    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "suppliers.json"))
    (tmp_path / "suppliers.json").write_text(json.dumps([
        {"rivile_code": "K0001", "name": "UAB Ąžuolas", "company_code": "111", "vat_code": "LT111",
         "iban": "", "email": "", "expense_account": "63028"},
        {"rivile_code": "K0002", "name": "Kita", "company_code": "222", "vat_code": "",
         "iban": "", "email": "", "expense_account": "6000"},
    ]), encoding="utf-8")


def inv(number="SF-1", code="K0001", lines=None, **kw):
    lines = lines if lines is not None else [
        InvoiceLine("Žibalas „Šiaurė“ & Ko", Decimal("2"), Decimal("10.005"), Decimal("0.21")),
        InvoiceLine("Pristatymas", Decimal("1"), Decimal("5.00"), Decimal("0.09"))]
    return ParsedInvoice(number=number, issue_date=date(2026, 10, 5), due_date=date(2026, 11, 4),
                         supplier_name="x", supplier_code=code, lines=lines, source_file="a/b.pdf", **kw)


def parse(result):
    """EIP be šaknies ir deklaracijos: testams apgaubiama <EIP> ir dekoduojama windows-1257."""
    text = result.xml.decode("windows-1257")
    text = re.sub(r"^<\?xml[^>]*\?>", "", text).strip()
    if not text.lstrip().startswith("<EIP>"):
        text = "<EIP>" + text + "</EIP>"
    return etree.fromstring(text.encode("utf-8"), etree.XMLParser(resolve_entities=False))


def test_structure_and_required_tags():
    root = parse(build_eip([inv()], dict(DEFAULT_REFS)))
    assert root.tag == "EIP"
    (doc,) = root.findall("I06")
    for tag in ("I06_KODAS_KS", "I06_OP_TIP", "I06_DOK_NR", "I06_OP_DATA", "I06_DOK_DATA", "I06_KODAS_XS"):
        assert doc.findtext(tag), tag
    assert doc.findtext("I06_KODAS_KS") == "K0001" and doc.findtext("I06_OP_TIP") == "1"
    assert doc.findtext("I06_DOK_DATA") == "2026.10.05"
    lines = doc.findall("I07")
    assert len(lines) == 2
    for ln in lines:
        for tag in ("I07_TIPAS", "I07_KODAS", "I07_KODAS_IS", "I07_KODAS_US", "T_KIEKIS", "I07_KAINA_BE",
                    "I07_PVM", "I07_SUMA", "I07_MOKESTIS_P"):
            assert ln.findtext(tag) not in (None, ""), tag
    assert doc.find("I08/I08_MOK_D").text == "30"


def test_rivile_style_no_declaration_no_root_one_tag_per_line():
    res = build_eip([inv()], dict(DEFAULT_REFS))
    lines = res.xml.split(bytes([13, 10]))
    assert lines[0] == b"<I06>" and lines[1].startswith(b"     <I06_KODAS_KS>")
    assert b"<?xml" not in res.xml and b"<EIP>" not in res.xml


def test_encoding_declaration_and_lithuanian_chars_preserved():
    res = build_eip([inv()], {**DEFAULT_REFS, "declaration": True, "root": "EIP"})
    assert res.xml.startswith(b'<?xml version="1.0" encoding="windows-1257"?>')
    assert "Žibalas „Šiaurė“ &amp; Ko".encode("windows-1257") in res.xml
    assert parse(res).find("I06/I07/I07_PAV").text == "Žibalas „Šiaurė“ & Ko"


def test_unencodable_char_becomes_char_ref_not_crash():
    line = InvoiceLine("Тест 😀", Decimal(1), Decimal(1), Decimal(0))
    res = build_eip([inv(lines=[line])], dict(DEFAULT_REFS))
    assert parse(res).find("I06/I07/I07_PAV").text == "Тест 😀"


def test_vat_mapping_and_tax_code():
    root = parse(build_eip([inv()], dict(DEFAULT_REFS)))
    l1, l2 = root.findall("I06/I07")
    assert (l1.findtext("I07_MOKESTIS_P"), l1.findtext("I07_KODAS_KL")) == ("21.00", "PVM1")
    assert (l2.findtext("I07_MOKESTIS_P"), l2.findtext("I07_KODAS_KL")) == ("9.00", "PVM2")
    assert root.findtext("I06/I06_KODAS_XS") == "PVM"
    zero_line = InvoiceLine("x", Decimal(1), Decimal(7), Decimal(0))
    zero = parse(build_eip([inv(lines=[zero_line])], dict(DEFAULT_REFS)))
    assert zero.findtext("I06/I06_KODAS_XS") == "PVM_0"
    assert zero.find("I06/I07/I07_KODAS_KL") is None


def test_totals_equal_line_sums():
    p = inv()
    root = parse(build_eip([p], dict(DEFAULT_REFS)))
    net = sum(Decimal(x.findtext("I07_SUMA")) for x in root.findall("I06/I07"))
    vat = sum(Decimal(x.findtext("I07_PVM")) for x in root.findall("I06/I07"))
    assert net == money(sum(ln.net for ln in p.lines)) and vat == money(sum(ln.vat for ln in p.lines))
    assert f"su PVM {money(net + vat)}" in root.findtext("I06/I06_APRASYMAS1")


def test_supplier_code_and_service_code_mapping():
    root = parse(build_eip([inv(), inv("SF-2", "K0002")], dict(DEFAULT_REFS)))
    a, b = root.findall("I06")
    assert a.find("I07/I07_KODAS").text == "63028"   # tiekėjo sąnaudų sąskaita yra N25 sąraše
    assert b.find("I07/I07_KODAS").text == "63027"   # 6000 nėra N25 sąraše, numatytasis kodas
    assert b.findtext("I06_KODAS_KS") == "K0002"


def test_only_ready_invoices_included():
    nodate = inv("D")
    nodate.due_date = None
    res = build_eip([inv("A"), inv("B", ""), inv("C", lines=[]), nodate], dict(DEFAULT_REFS))
    assert res.included == ["A"]
    assert {n for n, _ in res.skipped} == {"B", "C", "D"}
    assert [x.text for x in parse(res).iter("I06_DOK_NR")] == ["A"]


def test_no_root_variant_and_file_write(tmp_path):
    refs = {**DEFAULT_REFS, "root": ""}
    out = purchases_to_eip([inv()], tmp_path / "x" / "a.eip", refs)
    assert out.read_bytes().split(bytes([13, 10]))[0] == b"<I06>"


def test_load_refs_from_gama_file(tmp_path):
    f = tmp_path / "refs.json"
    f.write_text(json.dumps({"data": {
        "codes": [{"code": "7001", "tipas": "1"}, {"code": "7002", "tipas": "2"}],
        "line_combos": [{"tipas": "3", "warehouse": "W1", "unit": "KG", "n": 5},
                        {"tipas": "3", "warehouse": "W2", "unit": "VNT", "n": 2}]}}), encoding="utf-8")
    refs = load_refs(f)
    assert refs["codes"] == ["7001"] and refs["default_code"] == "7001"
    assert (refs["warehouse"], refs["unit"]) == ("W1", "KG")
    assert load_refs(tmp_path / "missing.json")["warehouse"] == "ADMIN"
