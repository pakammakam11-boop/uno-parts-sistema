"""Darbuotojų sąrašas (master) iš XLSX: sutarta neto, norma ir DU sąlygos kiekvienam darbuotojui.

Stulpeliai (viena antraštės eilutė, lapas „Darbuotojai“ arba pirmas lapas):
  Tab. Nr. | Vardas Pavardė | Rivilės kodas | Sutarta neto | Valandų norma | Terminuota (taip/ne) |
  Pensijų kaupimas (taip/ne) | NPD taikomas (taip/ne) | Ligos apmokėjimas % | Priimtas šį mėnesį (taip/ne)
Pirmi 5 stulpeliai privalomi; taip/ne stulpeliuose tuščias langelis = numatytoji reikšmė.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from core import world
from core.models import money
from core.payroll_rules import SICK_RATE_MAX, SICK_RATE_MIN, EmployeeTerms
from core.rivile_payroll import parse_number

SHEET = "Darbuotojai"
COL_TAB, COL_NAME, COL_CODE = "Tab. Nr.", "Vardas Pavardė", "Rivilės kodas"
COL_NET, COL_NORM = "Sutarta neto", "Valandų norma"
COL_FIXED, COL_PENSION, COL_NPD = "Terminuota", "Pensijų kaupimas", "NPD taikomas"
COL_SICK, COL_HIRED = "Ligos apmokėjimas %", "Priimtas šį mėnesį"
COLUMNS = [COL_TAB, COL_NAME, COL_CODE, COL_NET, COL_NORM, COL_FIXED, COL_PENSION, COL_NPD, COL_SICK, COL_HIRED]
REQUIRED = COLUMNS[:5]
BOOL_DEFAULTS = {COL_FIXED: False, COL_PENSION: False, COL_NPD: True, COL_HIRED: False}
SICK_DEFAULT_PERCENT = D("62.06")
MAX_NET = D("50000")
YES, NO = ("taip", "t", "yes", "1", "true"), ("ne", "n", "no", "0", "false")

INFO_ROWS = [
    "Darbuotojų sąrašas: viena eilutė darbuotojui. Antraštes palikite kaip šablone.",
    "Tab. Nr. turi sutapti su tabelio failo Tab. Nr.; Rivilės kodas yra darbuotojo kodas Rivilėje.",
    "Sutarta neto: suma „į rankas“ pilnam mėnesiui, eurais (pvz. 1 250,00).",
    "Valandų norma: mėnesio darbo valandų norma pagal etatą (pvz. 176 pilnam etatui).",
    "Terminuota, Pensijų kaupimas, NPD taikomas, Priimtas šį mėnesį: taip arba ne. "
    "Tuščia reikšmė: Terminuota ne, Pensijų kaupimas ne, NPD taikomas taip, Priimtas ne.",
    "Ligos apmokėjimas %: 62,06–100; tuščia = 62,06.",
]


class EmployeesError(ValueError):
    """Darbuotojų failo klaidos; `errors` yra sąrašas lietuviškų pranešimų (eilutė + stulpelis)."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class EmployeeRecord:
    tab_no: str
    name: str
    rivile_code: str
    net_agreed: D
    hours_norm: D
    fixed_term: bool = False
    pension_accumulation: bool = False
    npd_applies: bool = True
    sick_pay_percent: D = SICK_DEFAULT_PERCENT
    hired_this_month: bool = False

    def terms(self) -> EmployeeTerms:
        return EmployeeTerms(
            net_agreed=self.net_agreed, hours_norm=self.hours_norm, fixed_term=self.fixed_term,
            pension_accumulation=self.pension_accumulation, npd_applies=self.npd_applies,
            sick_pay_rate=self.sick_pay_percent / 100, hired_this_month=self.hired_this_month)


# ---------- nuskaitymas ----------

def _text(cell) -> str:
    return "" if cell is None else str(cell).strip()


def _bool(cell, column: str, row_no: int, errors: list[str]) -> bool:
    text = _text(cell).casefold()
    if not text:
        return BOOL_DEFAULTS[column]
    if text in YES:
        return True
    if text in NO:
        return False
    errors.append(f"Eilutė {row_no}, stulpelis „{column}“: reikšmė „{cell}“ turi būti taip arba ne")
    return BOOL_DEFAULTS[column]


def _number(cell, column: str, row_no: int, errors: list[str]) -> D | None:
    try:
        value = parse_number(cell)
    except ValueError:
        errors.append(f"Eilutė {row_no}, stulpelis „{column}“: neperskaitomas skaičius „{cell}“")
        return None
    return value


def _positive(cell, column: str, row_no: int, errors: list[str], maximum: D | None = None) -> D:
    value = _number(cell, column, row_no, errors)
    if value is None:
        return D(0)
    if value <= 0 or (maximum is not None and value > maximum):
        limit = f" ir ne didesnė už {maximum}" if maximum is not None else ""
        errors.append(f"Eilutė {row_no}, stulpelis „{column}“: reikšmė {value} turi būti didesnė už 0{limit}")
    return value


def _sick_percent(cell, row_no: int, errors: list[str]) -> D:
    if _text(cell) == "":
        return SICK_DEFAULT_PERCENT
    value = _number(cell, COL_SICK, row_no, errors)
    low, high = SICK_RATE_MIN * 100, SICK_RATE_MAX * 100
    if value is not None and not low <= value <= high:
        errors.append(f"Eilutė {row_no}, stulpelis „{COL_SICK}“: {value} turi būti nuo {low} iki {high}")
    return value if value is not None else SICK_DEFAULT_PERCENT


def _record(values: dict, row_no: int, errors: list[str]) -> EmployeeRecord:
    for column in (COL_TAB, COL_NAME, COL_CODE):
        if not _text(values[column]):
            errors.append(f"Eilutė {row_no}, stulpelis „{column}“: reikšmė privaloma")
    return EmployeeRecord(
        tab_no=_text(values[COL_TAB]), name=_text(values[COL_NAME]), rivile_code=_text(values[COL_CODE]),
        net_agreed=money(_positive(values[COL_NET], COL_NET, row_no, errors, MAX_NET)),
        hours_norm=_positive(values[COL_NORM], COL_NORM, row_no, errors, D("744")),
        fixed_term=_bool(values[COL_FIXED], COL_FIXED, row_no, errors),
        pension_accumulation=_bool(values[COL_PENSION], COL_PENSION, row_no, errors),
        npd_applies=_bool(values[COL_NPD], COL_NPD, row_no, errors),
        sick_pay_percent=_sick_percent(values[COL_SICK], row_no, errors),
        hired_this_month=_bool(values[COL_HIRED], COL_HIRED, row_no, errors))


def _duplicates(records: list[tuple[int, EmployeeRecord]], errors: list[str]) -> None:
    for attr, column in (("tab_no", COL_TAB), ("rivile_code", COL_CODE)):
        seen: dict[str, int] = {}
        for row_no, rec in records:
            key = getattr(rec, attr)
            if key and key in seen:
                errors.append(f"Eilutė {row_no}, stulpelis „{column}“: „{key}“ jau yra eilutėje {seen[key]}")
            seen.setdefault(key, row_no)


def _header_index(header: list) -> dict[str, int]:
    names = [_text(h) for h in header]
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise EmployeesError([f"Trūksta privalomų stulpelių: {', '.join(missing)}"])
    return {c: names.index(c) for c in COLUMNS if c in names}


def parse_employees(path: str | Path) -> list[EmployeeRecord]:
    """Nuskaito ir patikrina darbuotojų failą; visos klaidos surenkamos į vieną EmployeesError."""
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as exc:
        raise EmployeesError([f"Nepavyko atidaryti XLSX failo: {exc}"]) from None
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise EmployeesError(["Failas tuščias"])
    ix = _header_index(list(rows[0]))
    errors: list[str] = []
    records: list[tuple[int, EmployeeRecord]] = []
    for row_no, row in enumerate(rows[1:], start=2):
        if all(_text(c) == "" for c in row):
            continue
        values = {c: (row[i] if i < len(row) else None) for c, i in ix.items()}
        values = {c: values.get(c) for c in COLUMNS}
        records.append((row_no, _record(values, row_no, errors)))
    _duplicates(records, errors)
    if not records and not errors:
        errors.append("Faile nėra nė vieno darbuotojo")
    if errors:
        raise EmployeesError(errors)
    return [rec for _, rec in records]


# ---------- rašymas ----------

def _cells(rec: EmployeeRecord) -> list:
    yn = lambda flag: "taip" if flag else "ne"  # noqa: E731
    return [rec.tab_no, rec.name, rec.rivile_code, rec.net_agreed, rec.hours_norm, yn(rec.fixed_term),
            yn(rec.pension_accumulation), yn(rec.npd_applies), rec.sick_pay_percent, yn(rec.hired_this_month)]


def write_employees_xlsx(records: list[EmployeeRecord], path: str | Path, with_info: bool = True) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for rec in records:
        ws.append([float(c) if isinstance(c, D) else c for c in _cells(rec)])
    for idx in range(1, len(COLUMNS) + 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = 22
    ws.freeze_panes = "A2"
    if with_info:
        info = wb.create_sheet("Info")
        info.append(["Pastaba"])
        info["A1"].font = Font(bold=True)
        for text in INFO_ROWS:
            info.append([text])
        info.column_dimensions["A"].width = 120
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def write_employees_template(path: str | Path) -> Path:
    """Tuščias šablonas su antraštėmis ir lapu „Info“."""
    return write_employees_xlsx([], path)


def from_world(employees=None) -> list[EmployeeRecord]:
    """Demo / testų darbuotojų sąrašas iš core.world.EMPLOYEES (Rivilės kodas RIV-<tab. nr.>)."""
    return [EmployeeRecord(tab_no=e.tab_no, name=e.full_name, rivile_code=f"RIV-{e.tab_no}",
                           net_agreed=e.net_agreed, hours_norm=e.hours_norm, fixed_term=e.fixed_term)
            for e in (world.EMPLOYEES if employees is None else employees)]
