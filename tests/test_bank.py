from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from core import bank, world
from data import gen_bank

INV = world.PURCHASE_INVOICES


@pytest.fixture(scope="module")
def statement(tmp_path_factory):
    path = gen_bank.main(tmp_path_factory.mktemp("bank") / "israsas.xml")
    return path


@pytest.fixture(scope="module")
def expected_txs():
    net = sum((e.net_agreed for e in world.EMPLOYEES), Decimal(0))
    return world.bank_transactions(INV, net)


@pytest.fixture(scope="module")
def result(statement):
    return bank.reconcile(bank.parse_camt053(statement), INV, world.SALES_INVOICES)


def test_roundtrip_same_transactions(statement, expected_txs):
    assert bank.parse_camt053(statement) == expected_txs


def test_camt_structure(statement):
    root = etree.parse(str(statement)).getroot()
    assert root.tag.endswith("}Document") and "camt.053.001.02" in root.nsmap[None]
    ns = {"c": root.nsmap[None]}
    assert root.xpath("//c:Stmt/c:Acct/c:Id/c:IBAN", namespaces=ns)[0].text == world.COMPANY.iban.replace(" ", "")
    assert len(root.xpath("//c:Ntry", namespaces=ns)) == len(world.bank_transactions(INV, Decimal(0)))


def test_balances_consistent(statement, expected_txs):
    opening, closing = bank.parse_balances(statement)
    assert opening == Decimal("85000.00")
    assert closing == opening + sum((t.amount for t in expected_txs), Decimal(0))


def test_parses_camt_001_08_namespace(tmp_path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
<Ntry><Amt Ccy="EUR">12.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2026-10-05</Dt></BookgDt>
<AcctSvcrRef>X1</AcctSvcrRef><NtryDtls><TxDtls><RltdPties><Cdtr><Pty><Nm>Tiekėjas</Nm></Pty></Cdtr>
<CdtrAcct><Id><IBAN>LT117044060000001001</IBAN></Id></CdtrAcct></RltdPties>
<RmtInf><Ustrd>Sąsk. 1</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry></Stmt></BkToCstmrStmt></Document>"""
    path = tmp_path / "v8.xml"
    path.write_text(xml, encoding="utf-8")
    (tx,) = bank.parse_camt053(path)
    assert (tx.amount, tx.counterparty_name, tx.counterparty_iban, tx.remittance) == (
        Decimal("-12.50"), "Tiekėjas", "LT11 7044 0600 0000 1001", "Sąsk. 1")


def test_unpaid_invoices(result):
    assert {u.invoice.number for u in result.unpaid_invoices} == {INV[i].number for i in world.UNPAID_INVOICE_IDX}
    overdue = {u.invoice.number: u.days_overdue for u in result.unpaid_invoices}
    assert overdue[INV[7].number] == (world.PERIOD_END - INV[7].due_date).days > 0
    assert overdue[INV[31].number] == 0 or overdue[INV[31].number] > 0
    assert sum(1 for d in overdue.values() if d > 0) == sum(
        1 for i in world.UNPAID_INVOICE_IDX if INV[i].due_date < world.PERIOD_END)


def test_partial_payment(result):
    (p,) = result.partial
    inv = INV[world.PARTIAL_PAID_IDX]
    assert p.invoice.number == inv.number
    assert p.paid + p.remaining == inv.gross and p.remaining > 0
    warn = [f for f in result.findings if f.title == "Dalinis apmokėjimas"]
    assert len(warn) == 1 and warn[0].severity == "warning"


def test_duplicate_payment(result):
    (d,) = result.duplicates
    assert d.invoice.number == INV[world.DOUBLE_PAID_IDX].number
    (f,) = [f for f in result.findings if f.severity == "error"]
    assert f.ref == d.invoice.number and "Prašome grąžinti permoką" in f.suggested_action


def test_unmatched_supplier_payments(result):
    amounts = sorted(t.amount for t in result.unmatched_tx)
    assert amounts == sorted(a for _, a, _, _ in world.BANK_ONLY_PAYMENTS)
    missing = [f for f in result.findings if f.title == "Trūksta sąskaitos"]
    assert len(missing) == 2
    emails = {world.supplier_by_code(c).email for c, *_ in world.BANK_ONLY_PAYMENTS}
    assert all(any(e in f.suggested_action for e in emails) for f in missing)


def test_other_classified_not_errors(result):
    assert sorted(cat for _, cat in result.other) == sorted(
        ["Darbo užmokestis", "VMI (GPM)", "Sodra", "Banko mokestis"])
    other_refs = {t.ref for t, _ in result.other}
    assert not other_refs & {f.ref for f in result.findings}


def test_sales_matched(result):
    sales_numbers = {s.number for s in world.SALES_INVOICES}
    matched_sales = [m for m in result.matched if m[1].number in sales_numbers]
    assert len(matched_sales) == 4
    assert [u.invoice.number for u in result.unpaid_sales] == [world.SALES_INVOICES[4].number]


def test_every_invoice_matched_once(result):
    numbers = [inv.number for _, inv, _ in result.matched]
    assert len(numbers) == len(set(numbers))
    purchase_ok = len(INV) - len(world.UNPAID_INVOICE_IDX) - 1  # be dalinės
    assert sum(1 for n in numbers if n in {i.number for i in INV}) == purchase_ok
    assert {r for _, _, r in result.matched} <= {1, 2, 3}


def test_pain001_wellformed(tmp_path):
    to_pay = [INV[i] for i in world.UNPAID_INVOICE_IDX]
    path = bank.build_pain001(to_pay, tmp_path / "pay.xml", date(2026, 11, 2))
    root = etree.parse(str(path)).getroot()
    ns = {"p": bank.PAIN001_NS}
    total = sum((i.gross for i in to_pay), Decimal(0))
    assert root.xpath("//p:GrpHdr/p:NbOfTxs", namespaces=ns)[0].text == "3"
    assert Decimal(root.xpath("//p:GrpHdr/p:CtrlSum", namespaces=ns)[0].text) == total
    assert Decimal(root.xpath("//p:PmtInf/p:CtrlSum", namespaces=ns)[0].text) == total
    txs = root.xpath("//p:CdtTrfTxInf", namespaces=ns)
    assert len(txs) == 3
    assert sum(Decimal(t.xpath("p:Amt/p:InstdAmt", namespaces=ns)[0].text) for t in txs) == total
    first = txs[0]
    sup = world.supplier_by_code(to_pay[0].supplier_code)
    assert first.xpath("p:Cdtr/p:Nm", namespaces=ns)[0].text == sup.name
    assert first.xpath("p:CdtrAcct/p:Id/p:IBAN", namespaces=ns)[0].text == sup.iban.replace(" ", "")
    assert first.xpath("p:RmtInf/p:Ustrd", namespaces=ns)[0].text == f"Sąsk. {to_pay[0].number}"
    assert root.xpath("//p:Dbtr/p:Nm", namespaces=ns)[0].text == world.COMPANY.name


def test_pain001_rejects_sales(tmp_path):
    with pytest.raises(ValueError):
        bank.build_pain001(world.SALES_INVOICES, tmp_path / "x.xml", date(2026, 11, 2))


# --------------------------------------------------------------------------- Sol round 1 (S9-S13)

def _inv(number, sup, gross_net="100.00"):
    from core.models import Invoice, InvoiceLine
    line = InvoiceLine("Paslauga", Decimal("1"), Decimal(gross_net), Decimal("0"))
    return Invoice(number, date(2026, 10, 1), date(2026, 10, 20), sup.rivile_code, (line,))


def _tx(amount, sup, remittance, ref="T1"):
    from core.models import BankTransaction
    return BankTransaction(ref, date(2026, 10, 10), Decimal(amount), sup.name, sup.iban, remittance)


def test_s9_same_number_two_suppliers_not_both_paid():
    a, b = world.SUPPLIERS[0], world.SUPPLIERS[1]
    res = bank.reconcile([_tx("-100.00", a, "Sąsk. SF-1")], [_inv("SF-1", a), _inv("SF-1", b)], [])
    assert [(i.supplier_code) for _, i, _ in res.matched] == [a.rivile_code]
    assert [u.invoice.supplier_code for u in res.unpaid_invoices] == [b.rivile_code]


def test_s10_number_requires_token_boundaries():
    a = world.SUPPLIERS[0]
    short, longer = _inv("001-202610001", a), _inv("001-2026100019", a)
    res = bank.reconcile([_tx("-100.00", a, "Apmokėjimas 001-2026100019")], [short, longer], [])
    assert [i.number for _, i, _ in res.matched] == ["001-2026100019"]
    assert [u.invoice.number for u in res.unpaid_invoices] == ["001-202610001"]


def test_s11_overpayment_on_first_payment():
    a = world.SUPPLIERS[0]
    res = bank.reconcile([_tx("-10000.00", a, "Sąsk. SF-2")], [_inv("SF-2", a)], [])
    (f,) = [f for f in res.findings if f.title == "Permoka"]
    assert f.severity == "error" and "9900,00" in f.detail and "Prašome grąžinti permoką" in f.suggested_action


def test_s11_overpayment_when_completing_partials():
    a = world.SUPPLIERS[0]
    txs = [_tx("-60.00", a, "Sąsk. SF-3", "T1"), _tx("-60.00", a, "Sąsk. SF-3", "T2")]
    res = bank.reconcile(txs, [_inv("SF-3", a)], [])
    (f,) = [f for f in res.findings if f.title == "Permoka"]
    assert "20,00" in f.detail


def test_s11_exact_payment_no_overpay():
    a = world.SUPPLIERS[0]
    res = bank.reconcile([_tx("-100.00", a, "Sąsk. SF-4")], [_inv("SF-4", a)], [])
    assert not [f for f in res.findings if f.title == "Permoka"]


def _write_camt(tmp_path, ntry_body, ccy="EUR"):
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
           f'<BkToCstmrStmt><Stmt><Ntry><Amt Ccy="{ccy}">340.47</Amt><CdtDbtInd>DBIT</CdtDbtInd>'
           f'<BookgDt><Dt>2026-10-05</Dt></BookgDt><AcctSvcrRef>X9</AcctSvcrRef><NtryDtls>{ntry_body}</NtryDtls>'
           f'</Ntry></Stmt></BkToCstmrStmt></Document>')
    path = tmp_path / "c.xml"
    path.write_text(xml, encoding="utf-8")
    return path


_TXD = "<TxDtls><RmtInf><Ustrd>Sąsk. 1</Ustrd></RmtInf></TxDtls>"


def test_s12_non_eur_rejected(tmp_path):
    with pytest.raises(ValueError, match="ne EUR valiuta"):
        bank.parse_camt053(_write_camt(tmp_path, _TXD, ccy="USD"))


def test_s13_multiple_txdtls_rejected(tmp_path):
    with pytest.raises(ValueError, match="kelias"):
        bank.parse_camt053(_write_camt(tmp_path, _TXD * 2))
    assert len(bank.parse_camt053(_write_camt(tmp_path, _TXD))) == 1


def test_number_with_line_break_still_matches_n4():
    from core.bank import _number_in_text
    assert _number_in_text("SF-1", "apmokėjimas sf- 1")
    assert not _number_in_text("SF-1", "sf-12")
