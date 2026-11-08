"""Rivilė GAMA mėnesio snapshot'o (tools/gama_snapshot.ps1) skaitymas. Tik skaitymas.

Darbuotojas identifikuojamas tik A20_KODAS_TB. Kodai normalizuojami be pirmaujančių nulių
('000000000100' -> '100'). Raktas visada (kodas, tipas), nes 302 yra ir kompensacija (1), ir alimentai (2).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ACCRUAL, DEDUCTION = "1", "2"


@dataclass(frozen=True)
class PayLine:
    tb: str
    code: str
    tipas: str
    month: str       # 'YYYY-MM' (A20_DATE_SP)
    suma: Decimal
    laikas: Decimal | None = None   # A20_LAIKAS (valandos)


@dataclass(frozen=True)
class Variable:
    """A02_KINR eilutė: kintamasis su galiojimo data (darbuotojui arba įmonės konstanta, tb = '')."""
    code: str
    tipas: str
    date: str        # 'YYYY-MM-DD' (mėnesio pradžia)
    tb: str
    suma: Decimal
    unit: str | None = None   # A02_KODAS_IS (padalinys); None = snapshot'e rakto nėra (senas formatas)


@dataclass(frozen=True)
class HoursRow:
    """A18 (tabelis, tb nurodytas) arba A15 (kalendorius, tb = '') mėnesio sumos eilutė, valandomis."""
    tb: str
    month: str
    lt: str          # tabelio žymėjimo kodas (1 = FD, 606 = DD ...)
    calendar: str    # A18_KODAS_LA / A15_KODAS_LA
    hours: Decimal


def _norm_code(value) -> str:
    return str(value or "").strip().lstrip("0") or "0"


def _dec(value) -> Decimal:
    text = str(value).strip() if value is not None else ""
    return Decimal(text) if text else Decimal(0)


def _hours(row: dict, prefix: str) -> Decimal:
    """V00 yra 1/FRAKCIJA valandos dalimis ('60' = minutės)."""
    fraction = _dec(row.get(f"{prefix}_FRAKCIJA"))
    return _dec(row.get(f"{prefix}_V00")) / (fraction if fraction > 0 else Decimal(1))


@dataclass
class Snapshot:
    month: str
    history_from: str
    employees: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    timesheet: list = field(default_factory=list)
    calendar: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "Snapshot":
        lines = [PayLine(tb=str(r["A20_KODAS_TB"]).strip(),
                         code=(str(r["A20_KODAS_LP"]).strip().lstrip("0") or "0"),
                         tipas=str(r["A20_TIPAS"]).strip(),
                         month=str(r["A20_DATE_SP"])[:7],
                         suma=Decimal(str(r["A20_SUMA"] or "0").strip()),
                         laikas=(_dec(r.get("A20_LAIKAS")) if str(r.get("A20_LAIKAS") or "").strip() else None))
                 for r in raw.get("pay_lines") or []]
        variables = [Variable(code=_norm_code(r["A02_KODAS_LK"]), tipas=str(r["A02_TIPAS"]).strip(),
                              date=str(r["A02_DATE"])[:10], tb=str(r.get("A02_KODAS_TB") or "").strip(),
                              suma=_dec(r.get("A02_SUMA")),
                              unit=(str(r.get("A02_KODAS_IS") or "").strip() if "A02_KODAS_IS" in r else None))
                     for r in raw.get("variables") or []]
        timesheet = [HoursRow(tb=str(r["A18_KODAS_TB"]).strip(), month=str(r["A18_DATE"])[:7],
                              lt=_norm_code(r["A18_KODAS_LT"]), calendar=str(r.get("A18_KODAS_LA") or "").strip(),
                              hours=_hours(r, "A18"))
                     for r in raw.get("timesheet_hours") or []]
        calendar = [HoursRow(tb="", month=str(r["A15_DATE"])[:7], lt=_norm_code(r["A15_KODAS_LT"]),
                             calendar=str(r.get("A15_KODAS_LA") or "").strip(), hours=_hours(r, "A15"))
                    for r in raw.get("calendar_hours") or []]
        return cls(month=raw["month"], history_from=raw.get("history_from") or "",
                   employees=raw.get("employees") or [], lines=lines,
                   variables=variables, timesheet=timesheet, calendar=calendar)

    def months(self) -> list[str]:
        return sorted({l.month for l in self.lines})


def load_snapshot(path) -> Snapshot:
    with open(Path(path), encoding="utf-8-sig") as f:
        return Snapshot.from_dict(json.load(f))


def month_lines(snapshot: Snapshot, month: str) -> dict[str, dict[tuple[str, str], Decimal]]:
    """{tb: {(kodas, tipas): suma}} už mėnesį; pasikartojančios eilutės sumuojamos."""
    out: dict[str, dict[tuple[str, str], Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for l in snapshot.lines:
        if l.month == month:
            out[l.tb][(l.code, l.tipas)] += l.suma
    return {tb: dict(v) for tb, v in out.items()}


def month_pay_hours(snapshot: Snapshot, month: str) -> dict[str, dict[tuple[str, str], Decimal]]:
    """{tb: {(kodas, tipas): A20_LAIKAS suma}}; eilutės be LAIKAS praleidžiamos."""
    out: dict[str, dict[tuple[str, str], Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for l in snapshot.lines:
        if l.month == month and l.laikas is not None:
            out[l.tb][(l.code, l.tipas)] += l.laikas
    return {tb: dict(v) for tb, v in out.items()}


# A01_KINT.csv (research/gama/A01_KINT.csv), stulpelis A01_G_MEN = 1: kintamasis galioja tik savo mėnesį,
# reikšmė į kitus mėnesius neperkeliama (https://gidas.rivile.lt/alga_ir_personalas/darbuotoju_aprasymas/alga_darbuotoju_sarasas).
# Pagal CSV tokie (kodas, tipas): 801/805/806 TIPAS 4 (priedai), 915 TIPAS 4, 57305/57306/57307 TIPAS 3.
MONTHLY_ONLY_VARIABLES = frozenset({("801", "4"), ("805", "4"), ("806", "4"), ("915", "4"),
                                    ("57305", "3"), ("57306", "3"), ("57307", "3")})


def _latest_variable_rows(snapshot: Snapshot, tb: str, code: str, tipas: str, month: str) -> list[Variable]:
    """Kiekvieno padalinio (A02_KODAS_IS) vėliausia eilutė su A02_DATE <= mėnesio paskutinė diena.

    Tik mėnesiui galiojantiems kintamiesiems (MONTHLY_ONLY_VARIABLES) imamos tik to mėnesio eilutės.
    Senas formatas be A02_KODAS_IS: vienas padalinys (None).
    """
    limit = f"{month}-31"
    monthly = (code, tipas) in MONTHLY_ONLY_VARIABLES
    rows = [v for v in snapshot.variables
            if v.tb == tb and v.code == code and v.tipas == tipas and v.date <= limit
            and (not monthly or v.date[:7] == month)]
    latest: dict[str | None, Variable] = {}
    for v in rows:
        if v.unit not in latest or v.date >= latest[v.unit].date:
            latest[v.unit] = v
    return list(latest.values())


def variable_ambiguous(snapshot: Snapshot, tb: str, code: str, tipas: str, month: str) -> bool:
    """Ar padalinių vėliausios reikšmės skiriasi: reikšmė nevienareikšmė."""
    return len({v.suma for v in _latest_variable_rows(snapshot, tb, code, tipas, month)}) > 1


def variable_value(snapshot: Snapshot, tb: str, code: str, tipas: str, month: str) -> Decimal | None:
    """Darbuotojo kintamojo reikšmė mėnesiui: kiekvieno padalinio vėliausia eilutė (A02_DATE <= mėnesio pabaiga).

    Jei padalinių vėliausios reikšmės skiriasi, reikšmė nevienareikšmė: None (kviestojo pusėje skirti nuo
    "nėra" per variable_ambiguous). Vienodos reikšmės arba vienas padalinys: ta reikšmė.
    """
    rows = _latest_variable_rows(snapshot, tb, code, tipas, month)
    if not rows or len({v.suma for v in rows}) > 1:
        return None
    return rows[-1].suma


def timesheet_hours(snapshot: Snapshot, tb: str, month: str) -> dict[str, Decimal]:
    """{lt: valandų suma} darbuotojui už mėnesį."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for r in snapshot.timesheet:
        if r.tb == tb and r.month == month:
            out[r.lt] += r.hours
    return dict(out)


def norm_hours(snapshot: Snapshot, tb: str, month: str) -> Decimal | None:
    """Mėnesio darbo valandų norma = darbuotojo kalendoriaus (A15, pagal A18_KODAS_LA) FD eilutė (LT 1).

    Patikrinta su tikrais GAMA duomenimis (2026-09): S_SISTEMINIS FD = 176 h, S_20 (0,5 etato) = 88 h.
    Eilutė 606 (DD) yra dienų skaičius, ne valandos, todėl normai netinka.
    Kai darbuotojas mėnesį turi kelis kalendorius, norma nenustatoma (None).
    """
    rows = [r for r in snapshot.timesheet if r.tb == tb and r.month == month]
    codes = {r.calendar for r in rows if r.calendar}
    if len(codes) != 1:
        return None
    code = codes.pop()
    cal = [r.hours for r in snapshot.calendar if r.month == month and r.calendar == code and r.lt == "1"]
    return sum(cal, Decimal(0)) if cal else None


def norm_days(snapshot: Snapshot, tb: str, month: str) -> Decimal | None:
    """Mėnesio darbo dienų norma = darbuotojo kalendoriaus (A15) DD eilutė (LT 606), dienomis.

    GAMA etatinę algą skaičiuoja pagal dienas: alga x dirbtos dienos (tabelio DD) / normos dienos
    (patikrinta 2026-08: 1240 x 20 / 21 = 1180,95).
    """
    rows = [r for r in snapshot.timesheet if r.tb == tb and r.month == month]
    codes = {r.calendar for r in rows if r.calendar}
    if len(codes) != 1:
        return None
    code = codes.pop()
    cal = [r.hours for r in snapshot.calendar if r.month == month and r.calendar == code and r.lt == "606"]
    return sum(cal, Decimal(0)) if cal else None
