"""Deterministinis demo faktų generatorius (seed=2026). Išgalvoti duomenys, jokių tikrų asmenų ar sumų.

Visos funkcijos grynos: ta pati diena visada duoda tuos pačius skaičius, todėl duomenis galima papildyti
po vieną dieną (be perskaičiavimo) ir testuoti.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from core.dashboard.periods import is_trading_day, month_end
from core.hr import workdays

SEED = 2026
BRANCH_SHARE = {"vln": 0.62, "kns": 0.38}
BRANCH_MARGIN_ADJ = {"vln": 0.004, "kns": -0.006}
FTE = {"vln": 33.5, "kns": 21.0}
HEADCOUNT = {"vln": 35, "kns": 22}
BASE_ORDERS = 212                      # užsakymų per dieną esant sezono ir savaitės dienos koeficientams = 1
LINES_PER_ORDER = 3.4
RETURN_RATE = 0.10
MONTH_SEASON = {1: .90, 2: .95, 3: 1.08, 4: 1.12, 5: 1.06, 6: .97, 7: .92, 8: .90, 9: 1.07, 10: 1.12, 11: 1.05, 12: .88}
WEEKDAY_FACTOR = {0: 1.12, 1: 1.05, 2: 1.02, 3: 1.0, 4: 0.95, 5: 0.35, 6: 0.0}
HOURS_PER_DAY = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 8.0, 5: 2.0}   # šeštadienį dirba rotacinė pamaina

# gamintojas, dalis, bendroji marža, vidutinis užsakymo dydis EUR
MANUFACTURERS = [
    ("Bosch", .18, .245, 215), ("Febi", .14, .29, 165), ("Mann", .11, .28, 120), ("TRW", .09, .25, 190),
    ("Sachs", .09, .26, 240), ("Valeo", .08, .27, 200), ("NK", .07, .31, 110), ("Kamoka", .06, .335, 95),
    ("Kiti gamintojai", .18, .27, 175),
]
WEIGHTED_AOV = sum(share * aov for _, share, _, aov in MANUFACTURERS)

COST_GROUPS = {
    "du": ("DU ir Sodra", "Rivilė"), "rent": ("Nuoma", "Rivilė"),
    "transport": ("Transportas ir kuras", "Rivilė + Bankas"), "utilities": ("Komunalinės", "Rivilė"),
    "it": ("IT ir ryšiai", "Rivilė"), "ic_fees": ("Intercars mokesčiai", "Intercars"), "other": ("Kita", "Rivilė"),
}
FUEL_SUPPLIERS = ["Circle K Lietuva", "Viada LT", "Neste Lietuva"]
FUEL_SPIKE = 1.19                      # kuro kainų šuolis nuo scenarijaus pradžios

# išgalvoti pardavėjai (vardai skiriasi nuo HR demo): vardas, filialas, svoris, valandų koeficientas
SELLERS = [
    ("Karolis Mockus", "vln", 1.30, 1.0), ("Eglė Rimkutė", "vln", 1.15, 1.0), ("Tadas Gudaitis", "vln", 1.10, 1.0),
    ("Aistė Pocienė", "vln", 1.00, 1.0), ("Vytenis Lukšas", "vln", 0.95, 1.0), ("Monika Vyšniauskaitė", "vln", 0.90, 1.0),
    ("Darius Norkus", "vln", 0.85, 1.0), ("Rūta Zubavičienė", "vln", 0.75, 0.75), ("Paulina Grigaitė", "vln", 0.60, 1.0),
    ("Edvinas Šimkus", "kns", 1.25, 1.0), ("Gintarė Matulionytė", "kns", 1.10, 1.0), ("Arnas Petkevičius", "kns", 1.00, 1.0),
    ("Justina Kondratė", "kns", 0.95, 1.0), ("Saulius Bagdonas", "kns", 0.85, 1.0), ("Neringa Daukšaitė", "kns", 0.80, 0.75),
    ("Rimas Survila", "kns", 0.65, 1.0),
]
ACCOUNTING_FACTS = {"invoices_held": 3, "month_close_errors": 2, "bank_unmatched": 5, "link": "/apskaita"}


def day_rng(day: date, tag: str = "") -> random.Random:
    return random.Random(f"{SEED}|{tag}|{day.isoformat()}")


def season(day: date) -> float:
    return MONTH_SEASON[day.month] * WEEKDAY_FACTOR[day.weekday()] if is_trading_day(day) else 0.0


def expected_revenue(day: date, branch: str) -> float:
    """Tikėtina dienos apyvarta be atsitiktinumo (biudžeto pagrindas)."""
    return BASE_ORDERS * season(day) * BRANCH_SHARE[branch] * WEIGHTED_AOV


def sales_rows(day: date) -> list[dict]:
    """Dienos pardavimai pagal filialą ir gamintoją. Ne darbo dieną tuščia."""
    if not is_trading_day(day):
        return []
    rng = day_rng(day, "sales")
    day_noise = rng.uniform(0.88, 1.12)
    rows = []
    for branch, b_share in BRANCH_SHARE.items():
        for name, m_share, margin, aov in MANUFACTURERS:
            mean = BASE_ORDERS * season(day) * day_noise * b_share * m_share
            orders = max(0, round(mean * rng.uniform(0.9, 1.1)))
            revenue = round(orders * aov * rng.uniform(0.93, 1.07), 2)
            gp_rate = margin + BRANCH_MARGIN_ADJ[branch] + rng.uniform(-0.012, 0.012)
            rows.append({"day": day, "branch": branch, "manufacturer": name, "orders": orders,
                         "lines": round(orders * LINES_PER_ORDER * rng.uniform(0.92, 1.08)),
                         "revenue": revenue, "cogs": round(revenue * (1 - gp_rate), 2),
                         "returns_count": round(orders * RETURN_RATE * rng.uniform(0.7, 1.3)),
                         "returns_amount": round(revenue * RETURN_RATE * rng.uniform(0.75, 1.25), 2)})
    return rows


def ops_rows(day: date, sales: list[dict], spike_day: date | None = None) -> list[dict]:
    """Skambučiai ir maršrutai pagal filialą. spike_day: scenarijaus diena su daug praleistų skambučių."""
    if not is_trading_day(day):
        return []
    rng = day_rng(day, "ops")
    rows = []
    for branch, b_share in BRANCH_SHARE.items():
        orders = sum(r["orders"] for r in sales if r["branch"] == branch)
        calls = round(150 * b_share * season(day) / 1.05 * rng.uniform(0.9, 1.1))
        missed = round(calls * rng.uniform(0.04, 0.09))
        if day == spike_day:
            missed = 14 if branch == "vln" else 9
            calls = max(calls, missed * 6)
        stops = round(orders * 0.55 * rng.uniform(0.9, 1.1))
        rows.append({"day": day, "branch": branch, "calls_total": calls, "missed_calls": missed, "stops": stops,
                     "km": round(stops * rng.uniform(6.5, 8.5), 1), "late_stops": round(stops * rng.uniform(0.04, 0.11))})
    return rows


def _line(counters: dict, day: date, branch: str, grp: str, supplier: str, amount: float,
          source: str = "Rivilė", prefix: str = "RIV") -> dict:
    key = (day.year, day.month, prefix)
    counters[key] = counters.get(key, 0) + 1
    return {"day": day, "branch": branch, "grp": grp, "supplier": supplier, "source": source,
            "doc_no": f"{prefix}-{day.year}{day.month:02d}-{counters[key]:04d}", "amount": round(amount, 2)}


def _payroll_lines(rng, c: dict, d, k: float, bonus: float) -> list[dict]:
    out = []
    for branch, share in BRANCH_SHARE.items():
        out += [
            _line(c, d(10), branch, "du", "Atlyginimai (bruto), avansas", 118000 * .45 * share * k * bonus),
            _line(c, d(25), branch, "du", "Atlyginimai (bruto), likutis", 118000 * .55 * share * k * bonus),
            _line(c, d(15), branch, "du", "VSDFV darbdavio įmokos", 2090 * share * k),
            _line(c, d(28), branch, "du", "Priedai ir atostogų rezervas", 4700 * share * rng.uniform(.8, 1.2)),
        ]
    return out


def _overhead_lines(rng, c: dict, d, month: int) -> list[dict]:
    winter = 1.35 if month in (11, 12, 1, 2, 3) else 0.8
    out = []
    for branch, share in BRANCH_SHARE.items():
        heat = "Vilniaus energija (šiluma)" if branch == "vln" else "Kauno energija (šiluma)"
        out += [
            _line(c, d(5), branch, "utilities", "Ignitis (elektra)", 1750 * share * winter),
            _line(c, d(8), branch, "utilities", "Vandens tiekėjas", 420 * share * rng.uniform(.9, 1.1)),
            _line(c, d(12), branch, "utilities", heat, 2100 * share * winter),
            _line(c, d(7), branch, "it", "Telia (ryšiai)", 780 * share * rng.uniform(.95, 1.05)),
            _line(c, d(3), branch, "it", "Rivilė (licencijos ir priežiūra)", 1350 * share),
            _line(c, d(14), branch, "it", "Microsoft 365", 420 * share),
            _line(c, d(18), branch, "transport", "Transporto draudimas (Gjensidige)", 1180 * share),
        ]
    out += [_line(c, d(1), "vln", "rent", "Eišiškių pl. patalpų nuoma (UAB Pastatų valdymas)", 8900),
            _line(c, d(1), "kns", "rent", "Kauno sandėlio nuoma (UAB Logistikos parkas)", 5100)]
    return out


def _intercars_lines(rng, c: dict, d, k: float) -> list[dict]:
    fee = "Inter Cars Lietuva (franšizės mokestis)"
    logistics = "Inter Cars Lietuva (logistika ir marketingas)"
    return [_line(c, d(20), "vln", "ic_fees", fee, 3600 * k, "Intercars", "IC"),
            _line(c, d(20), "kns", "ic_fees", fee, 2200 * k, "Intercars", "IC"),
            _line(c, d(22), "vln", "ic_fees", logistics, 1650 * k, "Intercars", "IC"),
            _line(c, d(22), "kns", "ic_fees", logistics, 1000 * k, "Intercars", "IC")]


def _variable_lines(rng, c: dict, year: int, month: int, spike_from: date | None) -> list[dict]:
    first = date(year, month, 1)
    others = ["Biuro reikmenės", "Reklama (Google Ads)", "Bankų komisiniai", "Pakavimo medžiagos",
              "Valymo paslaugos", "Teisinės paslaugos"]
    out = []
    for day in workdays.days_between(first, month_end(first)):
        if not is_trading_day(day):
            continue
        for branch, share in BRANCH_SHARE.items():
            factor = FUEL_SPIKE if spike_from and day >= spike_from else 1.0   # kuro kortelių dienos suvestinė
            out.append(_line(c, day, branch, "transport", rng.choice(FUEL_SUPPLIERS),
                             rng.uniform(250, 330) * (share / .5) * factor, "Bankas", "BNK"))
            if rng.random() < 0.08:
                out.append(_line(c, day, branch, "transport", "Autoservisas (padangos, remontas)", rng.uniform(180, 760)))
            if rng.random() < 0.35:
                out.append(_line(c, day, branch, "other", rng.choice(others), rng.uniform(90, 1100) * share / .5))
    return out


def cost_lines(year: int, month: int, spike_from: date | None = None) -> list[dict]:
    """Visos mėnesio kaštų dokumentų eilutės (filtruoti pagal datą daro kvietėjas)."""
    rng = random.Random(f"{SEED}|costs|{year}-{month:02d}")
    last = month_end(date(year, month, 1)).day

    def d(n: int) -> date:
        return date(year, month, min(n, last))

    c: dict = {}
    k = rng.uniform(0.98, 1.02)
    lines = (_payroll_lines(rng, c, d, k, 1.07 if month == 12 else 1.0) + _overhead_lines(rng, c, d, month)
             + _intercars_lines(rng, c, d, k) + _variable_lines(rng, c, year, month, spike_from))
    return sorted(lines, key=lambda r: (r["day"], r["doc_no"]))


def budget_rows(year: int, month: int) -> list[dict]:
    """Mėnesio biudžetas pagal filialą: tikėtina apyvarta +2 %, bendrasis pelnas 27,2 %."""
    first = date(year, month, 1)
    rows = []
    for branch in BRANCH_SHARE:
        revenue = round(sum(expected_revenue(d, branch) for d in workdays.days_between(first, month_end(first))) * 1.02, 2)
        rows.append({"month": f"{year}-{month:02d}", "branch": branch, "revenue": revenue,
                     "gross_profit": round(revenue * 0.272, 2)})
    return rows


def deposits(today: date) -> list[dict]:
    """Depozitai (užstatas už grąžinamus senų detalių korpusus), kurių dalis laukia ilgiau nei 30 d."""
    rng = random.Random(f"{SEED}|deposits")
    kinds = ["Akumuliatoriai", "Generatoriai", "Starteriai", "Stabdžių suportai", "Turbokompresoriai", "Sankabos komplektai"]
    rows = []
    for i in range(1, 41):
        age = rng.choice([3, 6, 9, 12, 17, 21, 26, 29, 34, 38, 44, 52, 61, 75])
        returned = age < 10 and rng.random() < .5
        rows.append({"id": i, "branch": rng.choice(["vln", "vln", "kns"]), "supplier": rng.choice(kinds),
                     "qty": rng.randint(1, 8), "value": round(rng.uniform(35, 410), 2),
                     "created_day": today - timedelta(days=age), "status": "returned" if returned else "waiting"})
    return rows


MAILS = [  # siuntėjas, rūšis, prioritetas, tema, dėžutė, laukia val.
    ("Inter Cars Lietuva", "Intercars", "high", "Dėl Q4 bonusų ir tikslų patvirtinimo", "darbo", 26),
    ("Bosch tiekimas LT", "Tiekėjas", "high", "Kainų lentelė 2027 m. sausiui, reikia patvirtinti", "darbo", 19),
    ("UAB Autoservisas Aušra", "Klientas", "normal", "Prašymas dėl kreditinio limito didinimo", "darbo", 14),
    ("Febi Bilstein atstovas", "Tiekėjas", "normal", "Pasiūlymas dėl sezoninės akcijos", "darbo", 31),
    ("Inter Cars Lietuva", "Intercars", "normal", "Atsargų inventorizacijos grafikas", "darbo", 8),
    ("Kauno filialo vadovas", "Vidinis", "normal", "Papildomas sandėlio darbuotojas gruodžiui", "darbo", 22),
    ("UAB Taksi parkas", "Klientas", "low", "Sąskaitos faktūros kopija", "bendras", 40),
    ("Valeo Service", "Tiekėjas", "low", "Mokymų kvietimas", "bendras", 55),
    ("Draudimo brokeris", "Tiekėjas", "normal", "Turto draudimo pratęsimas", "darbo", 11),
    ("UAB Greitoji pagalba keliuose", "Klientas", "high", "Skundas dėl vėluojančio pristatymo", "darbo", 6),
]
TASKS = [  # pavadinimas, prioritetas, terminas dienomis nuo šiandien
    ("Patvirtinti gruodžio biudžetą", "high", 1), ("Peržiūrėti kuro sąnaudų augimą", "high", 0),
    ("Susitikimas su Inter Cars regiono vadovu", "normal", 3), ("Pasirašyti nuomos priedą (Kaunas)", "normal", -2),
    ("Atnaujinti pardavėjų premijų schemą", "low", 12), ("Aptarti depozitų grąžinimą su tiekėjais", "normal", 5),
]


def work_items(now: datetime) -> list[dict]:
    """Liutauro neatsakyti laiškai ir užduotys, santykinai pagal dabar (išgalvoti siuntėjai)."""
    mails = [{"kind": "mail", "priority": p, "title": subject, "counterparty": sender, "category": cat,
              "mailbox": box, "received_at": (now - timedelta(hours=h)).replace(microsecond=0), "due_day": None}
             for sender, cat, p, subject, box, h in MAILS]
    tasks = [{"kind": "task", "priority": p, "title": t, "counterparty": "", "category": "Užduotis", "mailbox": "",
              "received_at": None, "due_day": now.date() + timedelta(days=days)} for t, p, days in TASKS]
    return mails + tasks
