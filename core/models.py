"""Bendri duomenų modeliai. Visi moduliai keičiasi tik šiais tipais.

Pinigai visada Decimal, apvalinti iki 0.01 (ROUND_HALF_UP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Company:
    name: str
    code: str          # įmonės kodas
    vat_code: str      # PVM kodas
    iban: str
    address: str


@dataclass(frozen=True)
class Supplier:
    rivile_code: str   # tiekėjo kodas Rivilė kartotekoje, pvz. "TIEK001"
    name: str
    company_code: str
    vat_code: str
    iban: str
    email: str
    expense_account: str  # sąnaudų sąskaita, pvz. "6111"


@dataclass(frozen=True)
class Employee:
    tab_no: str          # tabelio numeris, pvz. "D001"
    first_name: str
    last_name: str
    personal_code: str   # fiktyvus, bet su teisingu kontroliniu skaitmeniu
    iban: str
    position: str
    department: str
    fixed_term: bool
    net_agreed: Decimal  # sutarta suma "į rankas" (UNO moka neto)
    hours_norm: Decimal  # mėnesio darbo valandų norma (pagal etatą)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    quantity: Decimal
    unit_price: Decimal   # be PVM
    vat_rate: Decimal     # 0.21 / 0.09 / 0.0

    @property
    def net(self) -> Decimal:
        return money(self.quantity * self.unit_price)

    @property
    def vat(self) -> Decimal:
        return money(self.net * self.vat_rate)


@dataclass(frozen=True)
class Invoice:
    number: str
    issue_date: date
    due_date: date
    supplier_code: str          # Supplier.rivile_code
    lines: tuple[InvoiceLine, ...]
    direction: str = "purchase"  # "purchase" | "sale"
    buyer_name: str = ""         # pardavimo sąskaitoms

    @property
    def net(self) -> Decimal:
        return money(sum((l.net for l in self.lines), Decimal(0)))

    @property
    def vat(self) -> Decimal:
        return money(sum((l.vat for l in self.lines), Decimal(0)))

    @property
    def gross(self) -> Decimal:
        return money(self.net + self.vat)


@dataclass(frozen=True)
class BankTransaction:
    ref: str                 # banko operacijos ID (AcctSvcrRef)
    booking_date: date
    amount: Decimal          # teigiama = įplauka, neigiama = mokėjimas
    counterparty_name: str
    counterparty_iban: str
    remittance: str          # mokėjimo paskirtis


@dataclass
class Finding:
    """Neatitikimas / pastaba, kurią rodo UI ir kontrolės modulis."""
    module: str              # "invoices" | "bank" | "payroll" | "timesheet" | "control"
    severity: str            # "error" | "warning" | "info"
    title: str
    detail: str
    ref: str = ""            # sąskaitos nr., darbuotojo tab_no, banko ref ...
    suggested_action: str = ""
    extra: dict = field(default_factory=dict)
