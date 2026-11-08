"""Bankas: camt.053 skaitymas, sulyginimas su sąskaitomis, pain.001 mokėjimo juodraštis.

Skaičiuoja kodas, ne AI. Visi tekstai lietuviškai.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from lxml import etree

from core import world
from core.models import BankTransaction, Finding, Invoice, Supplier, money

DATE_TOLERANCE_DAYS = 3
RULE_NUMBER, RULE_IBAN_AMOUNT, RULE_AMOUNT_NAME = 1, 2, 3
RULE_LABELS = {1: "sąskaitos nr. paskirtyje", 2: "IBAN + suma", 3: "suma + pavadinimas ±3 d."}
OVERPAY_TOLERANCE = Decimal("0.01")
PAIN001_NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"


# --------------------------------------------------------------------------- rezultatų tipai

@dataclass
class PartialPayment:
    invoice: Invoice
    transactions: list[BankTransaction]
    paid: Decimal
    remaining: Decimal


@dataclass
class DuplicatePayment:
    transaction: BankTransaction   # antras (perteklinis) mokėjimas
    invoice: Invoice
    original: BankTransaction | None


@dataclass
class UnpaidInvoice:
    invoice: Invoice
    days_overdue: int              # 0, jei terminas dar nesuėjęs (lyginant su world.PERIOD_END)
    party_name: str


@dataclass
class ReconResult:
    matched: list[tuple[BankTransaction, Invoice, int]] = field(default_factory=list)  # (tx, sąskaita, taisyklė 1-3)
    partial: list[PartialPayment] = field(default_factory=list)
    duplicates: list[DuplicatePayment] = field(default_factory=list)
    unmatched_tx: list[BankTransaction] = field(default_factory=list)
    unpaid_invoices: list[UnpaidInvoice] = field(default_factory=list)       # pirkimo sąskaitos
    unpaid_sales: list[UnpaidInvoice] = field(default_factory=list)          # pardavimo (gautinos)
    other: list[tuple[BankTransaction, str]] = field(default_factory=list)   # (tx, kategorija)
    overpayments: list[tuple[Invoice, Decimal]] = field(default_factory=list)  # (sąskaita, permoka)
    findings: list[Finding] = field(default_factory=list)


# --------------------------------------------------------------------------- camt.053 skaitymas

def _first(node, path: str):
    found = node.xpath(path)
    return found[0] if found else None


def _text(node, path: str) -> str:
    found = _first(node, path)
    return (found.text or "").strip() if found is not None else ""


def format_iban(iban: str) -> str:
    compact = "".join(iban.split()).upper()
    return " ".join(compact[i:i + 4] for i in range(0, len(compact), 4))


def _parse_date(text: str) -> date:
    return date.fromisoformat(text[:10])


def _check_entry_supported(ntry) -> None:
    ref = _text(ntry, "./*[local-name()='AcctSvcrRef']")
    ccy = _first(ntry, "./*[local-name()='Amt']/@Ccy")
    if ccy and str(ccy).strip().upper() != "EUR":
        raise ValueError(f"Operacija {ref} ne EUR valiuta: {ccy}")
    if len(ntry.xpath(".//*[local-name()='TxDtls']")) > 1:
        raise ValueError(f"Operacija {ref} turi kelias detalizacijas (TxDtls); "
                         "sudėtinės operacijos demonstracinėje versijoje nepalaikomos")


def _entry_to_tx(ntry) -> BankTransaction:
    _check_entry_supported(ntry)
    amount = money(_text(ntry, "./*[local-name()='Amt']"))
    if _text(ntry, "./*[local-name()='CdtDbtInd']") == "DBIT":
        amount = -amount
    side = "Cdtr" if amount < 0 else "Dbtr"
    party = f".//*[local-name()='RltdPties']/*[local-name()='{side}']"
    acct = f".//*[local-name()='RltdPties']/*[local-name()='{side}Acct']"
    remittance = " ".join(
        (u.text or "").strip() for u in ntry.xpath(".//*[local-name()='Ustrd']"))
    return BankTransaction(
        ref=_text(ntry, "./*[local-name()='AcctSvcrRef']"),
        booking_date=_parse_date(_text(ntry, "./*[local-name()='BookgDt']/*")),
        amount=amount,
        counterparty_name=_text(ntry, party + "//*[local-name()='Nm']"),
        counterparty_iban=format_iban(_text(ntry, acct + "//*[local-name()='IBAN']")),
        remittance=remittance.strip(),
    )


def parse_camt053(path) -> list[BankTransaction]:
    """Nuskaito camt.053 (.001.02 ir .001.08) pagal lokalius elementų vardus, be namespace priklausomybės."""
    root = etree.parse(str(path)).getroot()
    return [_entry_to_tx(n) for n in root.xpath("//*[local-name()='Ntry']")]


def parse_balances(path) -> tuple[Decimal, Decimal]:
    """(pradinis OPBD, galutinis CLBD) likutis su ženklu (DBIT = minusas)."""
    root = etree.parse(str(path)).getroot()
    found: dict[str, Decimal] = {}
    for bal in root.xpath("//*[local-name()='Bal']"):
        code = _text(bal, ".//*[local-name()='Cd']")
        value = money(_text(bal, "./*[local-name()='Amt']"))
        if _text(bal, "./*[local-name()='CdtDbtInd']") == "DBIT":
            value = -value
        found[code] = value
    return found["OPBD"], found["CLBD"]


# --------------------------------------------------------------------------- sulyginimas

def _norm_iban(iban: str) -> str:
    return "".join(iban.split()).upper()


def _norm_name(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _names_match(a: str, b: str) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def _party_name(inv: Invoice) -> str:
    return inv.buyer_name if inv.direction == "sale" else world.supplier_by_code(inv.supplier_code).name


def _party_iban(inv: Invoice) -> str:
    if inv.direction == "sale":
        return ""   # pirkėjų IBAN nežinomi
    return _norm_iban(world.supplier_by_code(inv.supplier_code).iban)


def classify_other(tx: BankTransaction) -> str | None:
    """DU / VMI / Sodra / banko mokestis: ne neatitikimai, o kita."""
    text = f"{tx.counterparty_name} {tx.remittance}".casefold()
    if "darbo užmokestis" in text:
        return "Darbo užmokestis"
    if "mokesčių inspekcija" in text or tx.remittance.upper().startswith("GPM"):
        return "VMI (GPM)"
    if "sodra" in text:
        return "Sodra"
    if "banko paslaug" in text or tx.counterparty_name.strip().casefold() == "bankas":
        return "Banko mokestis"
    return None


def _key(inv: Invoice) -> tuple[str, str]:
    """Sąskaitos raktas: (šalis, numeris); skirtingų tiekėjų vienodi numeriai nesusimaišo."""
    party = inv.buyer_name if inv.direction == "sale" else inv.supplier_code
    return party, inv.number


class _Ledger:
    """Seka, kiek kiekviena sąskaita jau apmokėta ir kokiais mokėjimais."""

    def __init__(self, invoices: list[Invoice]):
        self.invoices = invoices
        self.alloc: dict[tuple[str, str], list[tuple[BankTransaction, int]]] = {}
        self.paid: dict[tuple[str, str], Decimal] = {}
        self.duplicates: list[DuplicatePayment] = []

    def is_paid(self, inv: Invoice) -> bool:
        return self.paid.get(_key(inv), Decimal(0)) >= inv.gross

    def free(self) -> list[Invoice]:
        return [i for i in self.invoices if _key(i) not in self.alloc]

    def apply(self, tx: BankTransaction, inv: Invoice, rule: int) -> None:
        key = _key(inv)
        if self.is_paid(inv):
            first = self.alloc[key][0][0]
            self.duplicates.append(DuplicatePayment(tx, inv, first))
            return
        self.alloc.setdefault(key, []).append((tx, rule))
        self.paid[key] = self.paid.get(key, Decimal(0)) + abs(tx.amount)

    def find_duplicate_target(self, tx: BankTransaction) -> Invoice | None:
        """Jau pilnai apmokėta sąskaita to paties kontrahento ir tos pačios sumos."""
        iban = _norm_iban(tx.counterparty_iban)
        for inv in self.invoices:
            same_party = (iban and iban == _party_iban(inv)) or _names_match(tx.counterparty_name, _party_name(inv))
            if self.is_paid(inv) and inv.gross == abs(tx.amount) and same_party:
                return inv
        return None


def _same_party(tx: BankTransaction, inv: Invoice) -> bool:
    iban = _norm_iban(tx.counterparty_iban)
    return bool(iban and iban == _party_iban(inv)) or _names_match(tx.counterparty_name, _party_name(inv))


def _number_in_text(number: str, text: str) -> bool:
    """Numeris turi būti atskiras vienetas: be [0-9A-Za-z-] iš abiejų pusių."""
    chars = [re.escape(c) for c in "".join(number.split()).casefold()]
    pattern = r"\s*".join(chars)  # leidžiami formatavimo tarpai numerio viduje (N4)
    return bool(re.search(rf"(?<![0-9a-z-]){pattern}(?![0-9a-z-])", text))


def _by_number(tx: BankTransaction, ledger: _Ledger) -> Invoice | None:
    text = " ".join(tx.remittance.split()).casefold()
    found = [i for i in ledger.invoices if _number_in_text(i.number, text)]
    if len(found) > 1:   # tas pats numeris keliems tiekėjams: renkamės pagal mokėjimo gavėją
        found = [i for i in found if _same_party(tx, i)]
    return found[0] if len(found) == 1 else None


def _nearest_due(candidates: list[Invoice], tx: BankTransaction) -> Invoice | None:
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs((i.due_date - tx.booking_date).days))


def _by_iban_amount(tx: BankTransaction, ledger: _Ledger) -> Invoice | None:
    iban = _norm_iban(tx.counterparty_iban)
    if not iban:
        return None
    return _nearest_due([i for i in ledger.free()
                         if _party_iban(i) == iban and i.gross == abs(tx.amount)], tx)


def _by_amount_name(tx: BankTransaction, ledger: _Ledger) -> Invoice | None:
    return _nearest_due([
        i for i in ledger.free()
        if i.gross == abs(tx.amount)
        and _names_match(tx.counterparty_name, _party_name(i))
        and abs((i.due_date - tx.booking_date).days) <= DATE_TOLERANCE_DAYS], tx)


def _match_side(txs: list[BankTransaction], invoices: list[Invoice]) -> tuple[_Ledger, list[BankTransaction]]:
    """Trys praėjimai pagal taisyklių eilę; sąskaitos nr. turi pirmenybę prieš sumos spėjimus."""
    ledger = _Ledger(invoices)
    remaining = sorted(txs, key=lambda t: (t.booking_date, t.ref))
    for rule, finder in ((RULE_NUMBER, _by_number), (RULE_IBAN_AMOUNT, _by_iban_amount),
                         (RULE_AMOUNT_NAME, _by_amount_name)):
        left = []
        for tx in remaining:
            inv = finder(tx, ledger)
            if inv is None:
                left.append(tx)
            else:
                ledger.apply(tx, inv, rule)
        remaining = left
    still = []
    for tx in remaining:   # numeris nenurodytas, bet tokia sąskaita jau apmokėta -> dublis
        inv = ledger.find_duplicate_target(tx)
        if inv is None:
            still.append(tx)
        else:
            ledger.apply(tx, inv, RULE_IBAN_AMOUNT)
    return ledger, still


# --------------------------------------------------------------------------- tekstai ir Finding'ai

def _fmt(amount: Decimal) -> str:
    return f"{abs(amount):.2f}".replace(".", ",")


def _supplier_by_iban(iban: str) -> Supplier | None:
    key = _norm_iban(iban)
    from core import suppliers

    return next((s for s in suppliers.all_suppliers() if key and _norm_iban(s.iban) == key), None)


def refund_request_text(dup: DuplicatePayment) -> str:
    sup = _supplier_by_iban(dup.transaction.counterparty_iban)
    to = f"Kam: {sup.email}\n" if sup else ""
    return (f"{to}Tema: Prašymas grąžinti permoką pagal sąskaitą {dup.invoice.number}\n\n"
            f"Sveiki,\n\nsąskaita {dup.invoice.number} ({_fmt(dup.invoice.gross)} EUR) buvo apmokėta du kartus: "
            f"{dup.original.booking_date if dup.original else ''} ir {dup.transaction.booking_date}. "
            f"Prašome grąžinti permoką {_fmt(dup.transaction.amount)} EUR į mūsų sąskaitą "
            f"{world.COMPANY.iban} arba patvirtinti, kad ji bus užskaityta kitame mokėjime.\n\n"
            f"Pagarbiai,\n{world.COMPANY.name}")


def missing_invoice_text(tx: BankTransaction, sup: Supplier) -> str:
    return (f"Kam: {sup.email}\n"
            f"Tema: Prašome atsiųsti sąskaitą faktūrą už {tx.booking_date} mokėjimą\n\n"
            f"Sveiki,\n\n{tx.booking_date} pervedėme {_fmt(tx.amount)} EUR (paskirtis: „{tx.remittance}“), "
            f"tačiau atitinkamos sąskaitos faktūros negavome. Prašome ją atsiųsti, kad galėtume "
            f"tinkamai užregistruoti apskaitoje.\n\nPagarbiai,\n{world.COMPANY.name}")


def _finding_duplicate(d: DuplicatePayment) -> Finding:
    return Finding(
        "bank", "error", "Dvigubas apmokėjimas",
        f"Sąskaita {d.invoice.number} ({_party_name(d.invoice)}) apmokėta du kartus; "
        f"permoka {_fmt(d.transaction.amount)} EUR ({d.transaction.booking_date}).",
        ref=d.invoice.number, suggested_action=refund_request_text(d),
        extra={"amount": str(abs(d.transaction.amount)), "tx_ref": d.transaction.ref})


def _overpay_request_text(inv: Invoice, over: Decimal) -> str:
    if inv.direction == "sale":  # permoką gavome mes: grąžinti klientui arba užskaityti (N5)
        return (f"Klientui {inv.buyer_name}\nTema: Permoka pagal sąskaitą {inv.number}\n\n"
                f"Sveiki,\n\nsąskaitą {inv.number} ({_fmt(inv.gross)} EUR) apmokėjote {_fmt(inv.gross + over)} EUR. "
                f"Permoką {_fmt(over)} EUR galime grąžinti į jūsų sąskaitą arba užskaityti kitai sąskaitai. "
                f"Prašome patvirtinti, kaip jums patogiau.\n\nPagarbiai,\n{world.COMPANY.name}")
    sup = None if inv.direction == "sale" else world.supplier_by_code(inv.supplier_code)
    to = f"Kam: {sup.email}\n" if sup else ""
    return (f"{to}Tema: Prašymas grąžinti permoką pagal sąskaitą {inv.number}\n\n"
            f"Sveiki,\n\nsąskaita {inv.number} ({_fmt(inv.gross)} EUR) apmokėta {_fmt(inv.gross + over)} EUR. "
            f"Prašome grąžinti permoką {_fmt(over)} EUR į mūsų sąskaitą {world.COMPANY.iban} "
            f"arba patvirtinti, kad ji bus užskaityta kitame mokėjime.\n\nPagarbiai,\n{world.COMPANY.name}")


def _finding_overpay(inv: Invoice, over: Decimal) -> Finding:
    return Finding(
        "bank", "error", "Permoka",
        f"Sąskaita {inv.number} ({_party_name(inv)}) {_fmt(inv.gross)} EUR apmokėta per daug; "
        f"permoka {_fmt(over)} EUR.",
        ref=inv.number, suggested_action=_overpay_request_text(inv, over),
        extra={"amount": str(over)})


def _finding_partial(p: PartialPayment) -> Finding:
    return Finding(
        "bank", "warning", "Dalinis apmokėjimas",
        f"Sąskaita {p.invoice.number} ({_party_name(p.invoice)}): sumokėta {_fmt(p.paid)} EUR iš "
        f"{_fmt(p.invoice.gross)} EUR, liko {_fmt(p.remaining)} EUR.",
        ref=p.invoice.number, extra={"remaining": str(p.remaining), "paid": str(p.paid)})


def _finding_unpaid(u: UnpaidInvoice) -> Finding:
    inv = u.invoice
    overdue = u.days_overdue > 0
    detail = (f"Sąskaita {inv.number} ({u.party_name}) {_fmt(inv.gross)} EUR, terminas {inv.due_date}"
              + (f", vėluoja {u.days_overdue} d." if overdue else ", terminas dar nesuėjęs."))
    return Finding("bank", "warning" if overdue else "info", "Neapmokėta sąskaita", detail,
                   ref=inv.number, extra={"days_overdue": u.days_overdue})


def _finding_unmatched(tx: BankTransaction) -> Finding:
    if tx.amount > 0:
        return Finding("bank", "warning", "Įplauka be sąskaitos",
                       f"{tx.booking_date}: gauta {_fmt(tx.amount)} EUR iš {tx.counterparty_name} "
                       f"(„{tx.remittance}“), pardavimo sąskaitos nerasta.", ref=tx.ref)
    sup = _supplier_by_iban(tx.counterparty_iban)
    if sup is None:
        return Finding("bank", "warning", "Mokėjimas nežinomam gavėjui",
                       f"{tx.booking_date}: {_fmt(tx.amount)} EUR gavėjui {tx.counterparty_name}, "
                       f"gavėjo nėra tiekėjų kartotekoje.", ref=tx.ref)
    return Finding("bank", "warning", "Trūksta sąskaitos",
                   f"{tx.booking_date}: sumokėta {_fmt(tx.amount)} EUR tiekėjui {sup.name} "
                   f"(„{tx.remittance}“), sąskaitos faktūros nėra.",
                   ref=tx.ref, suggested_action=missing_invoice_text(tx, sup),
                   extra={"supplier": sup.rivile_code})


# --------------------------------------------------------------------------- reconcile

def _collect(ledger: _Ledger, result: ReconResult) -> list[Invoice]:
    """Perkelia sąskaitų būsenas į ReconResult; grąžina visiškai neliestas sąskaitas."""
    for inv in ledger.invoices:
        allocs = ledger.alloc.get(_key(inv))
        if not allocs:
            continue
        if ledger.is_paid(inv):
            result.matched.extend((tx, inv, rule) for tx, rule in allocs)
            over = money(ledger.paid[_key(inv)] - inv.gross)
            if over > 0:
                result.overpayments.append((inv, over))
        else:
            paid = ledger.paid[_key(inv)]
            result.partial.append(PartialPayment(inv, [tx for tx, _ in allocs], paid, money(inv.gross - paid)))
    result.duplicates.extend(ledger.duplicates)
    return ledger.free()


def _days_overdue(inv: Invoice) -> int:
    return max(0, (world.PERIOD_END - inv.due_date).days)


def reconcile(transactions: list[BankTransaction], purchase_invoices: list[Invoice],
              sales_invoices: list[Invoice]) -> ReconResult:
    result = ReconResult()
    debit, credit = [], []
    for tx in transactions:
        category = classify_other(tx)
        if category:
            result.other.append((tx, category))
        else:
            (debit if tx.amount < 0 else credit).append(tx)

    for txs, invoices, unpaid_list in ((debit, purchase_invoices, result.unpaid_invoices),
                                       (credit, sales_invoices, result.unpaid_sales)):
        ledger, unmatched = _match_side(txs, invoices)
        result.unmatched_tx.extend(unmatched)
        for inv in _collect(ledger, result):
            unpaid_list.append(UnpaidInvoice(inv, _days_overdue(inv), _party_name(inv)))

    result.findings = (
        [_finding_duplicate(d) for d in result.duplicates]
        + [_finding_overpay(i, o) for i, o in result.overpayments]
        + [_finding_partial(p) for p in result.partial]
        + [_finding_unmatched(t) for t in result.unmatched_tx]
        + [_finding_unpaid(u) for u in result.unpaid_invoices]
        + [_finding_unpaid(u) for u in result.unpaid_sales if u.days_overdue > 0])
    return result


# --------------------------------------------------------------------------- pain.001

def _sub(parent, tag: str, text: str | None = None, **attrs):
    el = etree.SubElement(parent, f"{{{PAIN001_NS}}}{tag}", **attrs)
    if text is not None:
        el.text = text
    return el


def _credit_transfer(pmt_inf, inv: Invoice) -> None:
    sup = world.supplier_by_code(inv.supplier_code)
    tx = _sub(pmt_inf, "CdtTrfTxInf")
    _sub(_sub(tx, "PmtId"), "EndToEndId", inv.number[:35])
    _sub(_sub(tx, "Amt"), "InstdAmt", f"{inv.gross:.2f}", Ccy="EUR")
    _sub(_sub(tx, "Cdtr"), "Nm", sup.name)
    _sub(_sub(_sub(tx, "CdtrAcct"), "Id"), "IBAN", _norm_iban(sup.iban))
    _sub(_sub(tx, "RmtInf"), "Ustrd", f"Sąsk. {inv.number}")


def build_pain001(invoices_to_pay: Iterable[Invoice], path, execution_date: date) -> Path:
    """Sukuria SEPA mokėjimo nurodymų juodraštį (pain.001.001.03).

    TIK JUODRAŠTIS: failas niekur nesiunčiamas ir niekas nemokama automatiškai.
    Mokėjimus peržiūri ir patvirtina žmogus banko sistemoje.
    """
    invoices = list(invoices_to_pay)
    if any(i.direction != "purchase" for i in invoices):
        raise ValueError("Mokėjimo juodraštis kuriamas tik pirkimo sąskaitoms")
    total = money(sum((i.gross for i in invoices), Decimal(0)))
    root = etree.Element(f"{{{PAIN001_NS}}}Document", nsmap={None: PAIN001_NS})
    init = _sub(root, "CstmrCdtTrfInitn")
    hdr = _sub(init, "GrpHdr")
    _sub(hdr, "MsgId", f"UNO-{execution_date:%Y%m%d}-DRAFT")
    _sub(hdr, "CreDtTm", f"{execution_date}T08:00:00")
    _sub(hdr, "NbOfTxs", str(len(invoices)))
    _sub(hdr, "CtrlSum", f"{total:.2f}")
    _sub(_sub(hdr, "InitgPty"), "Nm", world.COMPANY.name)

    pmt = _sub(init, "PmtInf")
    _sub(pmt, "PmtInfId", f"UNO-{execution_date:%Y%m%d}-1")
    _sub(pmt, "PmtMtd", "TRF")
    _sub(pmt, "NbOfTxs", str(len(invoices)))
    _sub(pmt, "CtrlSum", f"{total:.2f}")
    _sub(_sub(_sub(pmt, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _sub(pmt, "ReqdExctnDt", execution_date.isoformat())
    _sub(_sub(pmt, "Dbtr"), "Nm", world.COMPANY.name)
    _sub(_sub(_sub(pmt, "DbtrAcct"), "Id"), "IBAN", _norm_iban(world.COMPANY.iban))
    _sub(_sub(_sub(pmt, "DbtrAgt"), "FinInstnId"), "Othr").append(_othr_id())
    _sub(pmt, "ChrgBr", "SLEV")
    for inv in invoices:
        _credit_transfer(pmt, inv)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(root).write(str(out), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return out


def _othr_id():
    el = etree.Element(f"{{{PAIN001_NS}}}Id")
    el.text = "NOTPROVIDED"
    return el
