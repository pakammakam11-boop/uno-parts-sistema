"""Generuoja spalio banko išrašą (ISO 20022 camt.053.001.02) iš core.world.

Paleidimas iš projekto šaknies: py -3.12 -m data.gen_bank
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from lxml import etree

from core import world
from core.models import BankTransaction

NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"
OPENING_BALANCE = Decimal("85000.00")
DEFAULT_PATH = Path(__file__).resolve().parent / "generated" / "bankas" / "israsas_2026-10.xml"


def _sub(parent, tag: str, text: str | None = None, **attrs):
    el = etree.SubElement(parent, f"{{{NS}}}{tag}", **attrs)
    if text is not None:
        el.text = text
    return el


def _iban(iban: str) -> str:
    return "".join(iban.split())


def _balance(stmt, code: str, amount: Decimal, day: str) -> None:
    bal = _sub(stmt, "Bal")
    _sub(_sub(_sub(bal, "Tp"), "CdOrPrtry"), "Cd", code)
    _sub(bal, "Amt", f"{abs(amount):.2f}", Ccy="EUR")
    _sub(bal, "CdtDbtInd", "DBIT" if amount < 0 else "CRDT")
    _sub(_sub(bal, "Dt"), "Dt", day)


def _party(parent, role: str, name: str, iban: str) -> None:
    _sub(_sub(parent, role), "Nm", name)
    if iban:
        _sub(_sub(_sub(parent, f"{role}Acct"), "Id"), "IBAN", _iban(iban))


def _entry(stmt, tx: BankTransaction) -> None:
    debit = tx.amount < 0
    day = tx.booking_date.isoformat()
    ntry = _sub(stmt, "Ntry")
    _sub(ntry, "NtryRef", tx.ref)
    _sub(ntry, "Amt", f"{abs(tx.amount):.2f}", Ccy="EUR")
    _sub(ntry, "CdtDbtInd", "DBIT" if debit else "CRDT")
    _sub(ntry, "Sts", "BOOK")
    _sub(_sub(ntry, "BookgDt"), "Dt", day)
    _sub(_sub(ntry, "ValDt"), "Dt", day)
    _sub(ntry, "AcctSvcrRef", tx.ref)
    _sub(_sub(_sub(ntry, "BkTxCd"), "Prtry"), "Cd", "NTRF")
    details = _sub(_sub(ntry, "NtryDtls"), "TxDtls")
    _sub(_sub(details, "Refs"), "AcctSvcrRef", tx.ref)
    parties = _sub(details, "RltdPties")
    if debit:
        _party(parties, "Dbtr", world.COMPANY.name, world.COMPANY.iban)
        _party(parties, "Cdtr", tx.counterparty_name, tx.counterparty_iban)
    else:
        _party(parties, "Dbtr", tx.counterparty_name, tx.counterparty_iban)
        _party(parties, "Cdtr", world.COMPANY.name, world.COMPANY.iban)
    _sub(_sub(details, "RmtInf"), "Ustrd", tx.remittance)


def build_camt053(transactions: list[BankTransaction], opening: Decimal = OPENING_BALANCE) -> etree._ElementTree:
    closing = opening + sum((t.amount for t in transactions), Decimal(0))
    root = etree.Element(f"{{{NS}}}Document", nsmap={None: NS})
    msg = _sub(root, "BkToCstmrStmt")
    hdr = _sub(msg, "GrpHdr")
    _sub(hdr, "MsgId", "UNO-STMT-2026-10")
    _sub(hdr, "CreDtTm", datetime(2026, 11, 1, 6, 0, 0).isoformat())
    stmt = _sub(msg, "Stmt")
    _sub(stmt, "Id", "UNO-STMT-2026-10-001")
    _sub(stmt, "ElctrncSeqNb", "1")
    _sub(stmt, "CreDtTm", datetime(2026, 11, 1, 6, 0, 0).isoformat())
    period = _sub(stmt, "FrToDt")
    _sub(period, "FrDtTm", f"{world.PERIOD_START}T00:00:00")
    _sub(period, "ToDtTm", f"{world.PERIOD_END}T23:59:59")
    acct = _sub(stmt, "Acct")
    _sub(_sub(acct, "Id"), "IBAN", _iban(world.COMPANY.iban))
    _sub(acct, "Ccy", "EUR")
    _sub(_sub(acct, "Ownr"), "Nm", world.COMPANY.name)
    _balance(stmt, "OPBD", opening, world.PERIOD_START.isoformat())
    _balance(stmt, "CLBD", closing, world.PERIOD_END.isoformat())
    for tx in transactions:
        _entry(stmt, tx)
    return etree.ElementTree(root)


def main(path: Path | str | None = None) -> Path:
    out = Path(path) if path else DEFAULT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    net_total = sum((e.net_agreed for e in world.EMPLOYEES), Decimal(0))
    txs = world.bank_transactions(world.PURCHASE_INVOICES, net_total)
    build_camt053(txs).write(str(out), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return out


if __name__ == "__main__":
    print(main())
