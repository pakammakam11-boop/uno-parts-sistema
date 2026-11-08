"""Fiktyvus, deterministinis UNO Parts mėnesio "pasaulis" demo tikslams (2026-10).

Visi moduliai ir generatoriai remiasi TIK šiuo failu, todėl sąskaitos, bankas,
tabelis ir DU tarpusavyje dera. Duomenys išgalvoti, sutapimai atsitiktiniai.

Sąmoningai įdėti neatitikimai (demo turi juos rasti):
- 3 pirkimo sąskaitos neapmokėtos (2 vėluoja), 1 apmokėta dalinai
- 2 banko mokėjimai be sąskaitos (trūkstami dokumentai -> priminimas tiekėjui)
- 1 dvigubai apmokėta sąskaita
- 1 sąskaita su PVM aritmetikos klaida PDF'e
- tabelyje: viršvalandžiai virš ribos, atostogos be prašymo, liga be Sodros pažymos,
  darbuotojas su 0 val., neatitikimas su Rivilė eksportu
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal as D

from core.models import BankTransaction, Company, Employee, Invoice, InvoiceLine, Supplier, money

PERIOD_YEAR, PERIOD_MONTH = 2026, 10
PERIOD_START = date(2026, 10, 1)
PERIOD_END = date(2026, 10, 31)
WORKDAYS_OCT_2026 = 22  # 2026-10: 22 darbo dienos, švenčių nėra
HOURS_NORM_FULL = D(WORKDAYS_OCT_2026 * 8)  # 176 val.

COMPANY = Company(
    name="UAB „Demo detalės“",
    code="300000001",
    vat_code="LT100000000017",
    iban="LT00 7300 0100 0000 0001",
    address="Pramonės g. 1, Vilnius",
)

FIRST_M = ["Jonas", "Tomas", "Mantas", "Lukas", "Darius", "Andrius", "Mindaugas", "Marius", "Justinas",
           "Rokas", "Vytautas", "Giedrius", "Saulius", "Tadas", "Karolis", "Donatas", "Evaldas", "Gintaras"]
LAST_M = ["Kazlauskas", "Petrauskas", "Jankauskas", "Stankevičius", "Vasiliauskas", "Žukauskas",
          "Butkus", "Paulauskas", "Urbonas", "Kavaliauskas", "Navickas", "Ramanauskas", "Sakalauskas"]
FIRST_F = ["Rūta", "Asta", "Jurgita", "Rasa", "Inga", "Eglė", "Agnė", "Greta", "Ieva", "Kristina", "Laura"]
LAST_F = ["Kazlauskienė", "Petrauskaitė", "Jankauskienė", "Stankevičiūtė", "Vasiliauskienė",
          "Žukauskaitė", "Butkienė", "Paulauskaitė", "Urbonienė", "Navickaitė"]

DEPARTMENTS = [  # (padalinys, pareigos, neto range, kiek žmonių)
    ("Sandėlis", "Sandėlininkas", (1050, 1350), 22),
    ("Logistika", "Vairuotojas-ekspeditorius", (1250, 1650), 14),
    ("Pardavimai", "Pardavimų vadybininkas", (1400, 2100), 9),
    ("Dirbtuvės", "Mechanikas", (1350, 1900), 7),
    ("Administracija", "Administratorius", (1300, 2400), 5),
]


def _personal_code(rng: random.Random, female: bool, birth: date) -> str:
    """Fiktyvus asmens kodas su teisingu kontroliniu skaitmeniu (formatas, ne tikras asmuo)."""
    century_digit = (4 if female else 3) if birth.year < 2000 else (6 if female else 5)
    body = f"{century_digit}{birth:%y%m%d}{rng.randint(0, 999):03d}"
    digits = [int(c) for c in body]
    for weights in ([1, 2, 3, 4, 5, 6, 7, 8, 9, 1], [3, 4, 5, 6, 7, 8, 9, 1, 2, 3]):
        r = sum(d * w for d, w in zip(digits, weights)) % 11
        if r != 10:
            return body + str(r)
    return body + "0"


def _iban(rng: random.Random) -> str:
    n = f"{rng.randint(0, 99):02d}{rng.randint(10**15, 10**16 - 1)}"
    return f"LT{n[:2]} {n[2:6]} {n[6:10]} {n[10:14]} {n[14:18]}"


def build_employees(seed: int = 57) -> list[Employee]:
    rng = random.Random(seed)
    out: list[Employee] = []
    used: set[str] = set()
    for dept, position, (lo, hi), count in DEPARTMENTS:
        for _ in range(count):
            female = rng.random() < 0.3
            while True:
                fn = rng.choice(FIRST_F if female else FIRST_M)
                ln = rng.choice(LAST_F if female else LAST_M)
                if f"{fn} {ln}" not in used:
                    used.add(f"{fn} {ln}")
                    break
            birth = date(rng.randint(1965, 2003), rng.randint(1, 12), rng.randint(1, 28))
            part_time = rng.random() < 0.08
            out.append(Employee(
                tab_no=f"D{len(out) + 1:03d}",
                first_name=fn,
                last_name=ln,
                personal_code=_personal_code(rng, female, birth),
                iban=_iban(rng),
                position=position,
                department=dept,
                fixed_term=rng.random() < 0.12,
                net_agreed=money(rng.randrange(lo, hi, 25)),
                hours_norm=HOURS_NORM_FULL / 2 if part_time else HOURS_NORM_FULL,
            ))
    assert len(out) == 57
    return out


SUPPLIERS: list[Supplier] = [
    Supplier("TIEK001", "UAB „Baltijos detalės“", "302000101", "LT200001011", "LT11 7044 0600 0000 1001", "saskaitos@baltdet.example", "6000"),
    Supplier("TIEK002", "UAB „Autoeksperta Wholesale“", "302000102", "LT200001021", "LT12 7300 0100 0000 1002", "buhalterija@autoeks.example", "6000"),
    Supplier("TIEK003", "UAB „Kuro taškas“", "302000103", "LT200001031", "LT13 7044 0600 0000 1003", "invoice@kurotaskas.example", "6305"),
    Supplier("TIEK004", "UAB „Telia demo“", "302000104", "LT200001041", "LT14 7300 0100 0000 1004", "billing@telia-demo.example", "6309"),
    Supplier("TIEK005", "UAB „Ignitis demo“", "302000105", "LT200001051", "LT15 7044 0600 0000 1005", "saskaitos@ignitis-demo.example", "6303"),
    Supplier("TIEK006", "UAB „Sandėlių nuoma“", "302000106", "LT200001061", "LT16 7300 0100 0000 1006", "nuoma@sandeliai.example", "6304"),
    Supplier("TIEK007", "UAB „DPD demo“", "302000107", "LT200001071", "LT17 7044 0600 0000 1007", "lt-invoice@dpd-demo.example", "6306"),
    Supplier("TIEK008", "UAB „Biuro prekės“", "302000108", "LT200001081", "LT18 7300 0100 0000 1008", "info@biuroprekes.example", "6308"),
    Supplier("TIEK009", "UAB „IT priežiūra“", "302000109", "LT200001091", "LT19 7044 0600 0000 1009", "it@itprieziura.example", "6309"),
    Supplier("TIEK010", "UAB „Lizingas demo“", "302000110", "LT200001101", "LT20 7300 0100 0000 1010", "lizingas@lizingas.example", "6310"),
    Supplier("TIEK011", "UAB „Darbo rūbai“", "302000111", "LT200001111", "LT21 7044 0600 0000 1011", "pardavimai@rubai.example", "6308"),
    Supplier("TIEK012", "UAB „Padangų centras“", "302000112", "LT200001121", "LT22 7300 0100 0000 1012", "saskaitos@padangos.example", "6000"),
]

SUPPLIER_ITEMS = {
    "TIEK001": [("Stabdžių kaladėlės", 18, 42), ("Tepalo filtras", 4, 9), ("Amortizatorius", 35, 80)],
    "TIEK002": [("Detalių siunta pagal užsakymą", 400, 2600)],
    "TIEK003": [("Dyzelinas, l", 1.35, 1.55)],
    "TIEK004": [("Ryšio paslaugos 2026-10", 180, 260)],
    "TIEK005": [("Elektros energija 2026-10", 900, 1400)],
    "TIEK006": [("Sandėlio nuoma 2026-10", 4200, 4200)],
    "TIEK007": [("Siuntų pervežimas", 2.9, 6.5)],
    "TIEK008": [("Kanceliarinės prekės", 40, 180)],
    "TIEK009": [("IT priežiūra 2026-10", 650, 650)],
    "TIEK010": [("Lizingo įmoka", 980, 980)],
    "TIEK011": [("Darbo rūbai ir avalynė", 300, 900)],
    "TIEK012": [("Padangos", 55, 120)],
}


def build_purchase_invoices(seed: int = 60) -> list[Invoice]:
    rng = random.Random(seed)
    out: list[Invoice] = []
    for i in range(60):
        sup = SUPPLIERS[i % len(SUPPLIERS)] if i < 24 else rng.choice(SUPPLIERS)
        items = SUPPLIER_ITEMS[sup.rivile_code]
        lines = []
        for _ in range(rng.randint(1, min(3, len(items)))):
            desc, lo, hi = rng.choice(items)
            qty = D(rng.randint(1, 12)) if lo < 100 else D(1)
            if desc.startswith("Dyzelinas"):
                qty = D(rng.randint(200, 900))
            unit = money(rng.uniform(lo, hi))
            lines.append(InvoiceLine(desc, qty, unit, D("0.21")))
        issue = PERIOD_START + timedelta(days=rng.randint(0, 27))
        out.append(Invoice(
            number=f"{sup.rivile_code[-3:]}-{2026}{issue:%m}{i + 1:03d}",
            issue_date=issue,
            due_date=issue + timedelta(days=rng.choice([7, 14, 14, 30])),
            supplier_code=sup.rivile_code,
            lines=tuple(lines),
        ))
    return out


def build_sales_invoices() -> list[Invoice]:
    buyers = ["UAB „Servisas Plius“", "UAB „Auto Remontas LT“", "MB „Ratų meistrai“", "UAB „Flotilė“", "UAB „Kelių technika“"]
    out = []
    for i, buyer in enumerate(buyers):
        issue = date(2026, 10, 5 + i * 5)
        out.append(Invoice(
            number=f"UNO-2026-{1040 + i}",
            issue_date=issue,
            due_date=issue + timedelta(days=14),
            supplier_code="",
            buyer_name=buyer,
            direction="sale",
            lines=(InvoiceLine("Autodalių pardavimas pagal užsakymą", D(1), money(3200 + i * 1450), D("0.21")),),
        ))
    return out


def supplier_by_code(code: str) -> Supplier:
    return next(s for s in SUPPLIERS if s.rivile_code == code)


# --- Sąmoningi scenarijai (indeksai į build_purchase_invoices()) ---
UNPAID_INVOICE_IDX = [7, 31, 52]       # neapmokėtos; 7 ir 31 jau vėluoja
PARTIAL_PAID_IDX = 44                   # apmokėta 50 %
DOUBLE_PAID_IDX = 12                    # apmokėta du kartus
PDF_VAT_ERROR_IDX = 19                  # PDF'e PVM suma klaidinga (+3,40 €)
BANK_ONLY_PAYMENTS = [                  # mokėjimai banke, kuriems nėra sąskaitos -> trūksta dokumento
    ("TIEK009", D("-418.50"), "Papildomi IT darbai spalis", date(2026, 10, 21)),
    ("TIEK003", D("-1260.00"), "Kuro kortelės papildymas", date(2026, 10, 27)),
]


@dataclass(frozen=True)
class TimesheetAnomaly:
    tab_no: str
    kind: str   # "overtime_over_limit" | "vacation_no_request" | "sick_no_certificate" | "zero_hours" | "rivile_mismatch"
    note: str


TIMESHEET_ANOMALIES = [
    TimesheetAnomaly("D004", "overtime_over_limit", "Viršvalandžiai 14 val. per savaitę (riba 12 val. per 7 d.)"),
    TimesheetAnomaly("D017", "vacation_no_request", "Tabelyje 5 d. atostogų (A), prašymo nėra"),
    TimesheetAnomaly("D029", "sick_no_certificate", "Tabelyje 3 d. ligos (L), Sodroje nedarbingumo pažymėjimo nėra"),
    TimesheetAnomaly("D041", "zero_hours", "0 val. visą mėnesį, nors darbo santykiai galioja"),
    TimesheetAnomaly("D050", "rivile_mismatch", "Tabelis 176 val., Rivilė eksporte 168 val."),
]


def bank_transactions(invoices: list[Invoice], employees_net_total: D) -> list[BankTransaction]:
    """Spalio banko išrašas: tiekėjų mokėjimai, įplaukos, DU, mokesčiai, scenarijai."""
    rng = random.Random(53)
    tx: list[BankTransaction] = []

    def add(d: date, amount: D, name: str, iban: str, text: str) -> None:
        tx.append(BankTransaction(f"BNK{len(tx) + 1:05d}", d, money(amount), name, iban, text))

    for idx, inv in enumerate(invoices):
        if idx in UNPAID_INVOICE_IDX:
            continue
        sup = supplier_by_code(inv.supplier_code)
        pay_day = min(inv.due_date - timedelta(days=rng.randint(0, 3)), PERIOD_END)
        amount = inv.gross / 2 if idx == PARTIAL_PAID_IDX else inv.gross
        # paskirtis kartais be sąskaitos numerio -> sulyginimas pagal sumą + IBAN
        text = f"Apmokėjimas pagal sąsk. {inv.number}" if rng.random() > 0.2 else f"{sup.name} spalis"
        add(pay_day, -amount, sup.name, sup.iban, text)
        if idx == DOUBLE_PAID_IDX:
            add(pay_day + timedelta(days=2), -amount, sup.name, sup.iban, f"Apmokėjimas pagal sąsk. {inv.number}")

    for code, amount, text, d in BANK_ONLY_PAYMENTS:
        sup = supplier_by_code(code)
        add(d, amount, sup.name, sup.iban, text)

    for inv in build_sales_invoices()[:4]:  # 5-oji pardavimo sąskaita dar neapmokėta
        add(min(inv.due_date, PERIOD_END), inv.gross, inv.buyer_name, _iban(rng), f"Už sąsk. {inv.number}")

    add(date(2026, 10, 15), -employees_net_total, "Darbo užmokestis (masinis mokėjimas)", "", "DU 2026-09")
    add(date(2026, 10, 15), D("-31846.27"), "Valstybinė mokesčių inspekcija", "LT24 7300 0101 1239 4300", "GPM 2026-09")
    add(date(2026, 10, 15), D("-21590.18"), "Sodra", "LT20 7300 0101 1239 4310", "Sodros įmokos 2026-09")
    add(date(2026, 10, 31), D("-12.40"), "Bankas", "", "Banko paslaugų mokestis")
    return sorted(tx, key=lambda t: (t.booking_date, t.ref))


EMPLOYEES = build_employees()
PURCHASE_INVOICES = build_purchase_invoices()
SALES_INVOICES = build_sales_invoices()
