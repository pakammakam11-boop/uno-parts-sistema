"""Darbo laiko apskaitos tabelis: įkėlimas ir patikra.

Tabelio XLSX formatas (generuoja data/gen_timesheet.py):
  Tab. Nr. | Vardas Pavardė | Padalinys | 1..31 | Viršvalandžiai 1..5 sav. | Viso val. | Atostogų d. | Ligos d.
Dienos langelis: skaičius = išdirbtos įprastos valandos, A = atostogos, L = liga, P/tuščia = poilsis.
Viršvalandžiai NĖRA dienų langeliuose, jie atskirais stulpeliais pagal savaites.
Savaitės (pirm.-sekm.) skaičiuojamos pagal tikrą mėnesį (week_ranges), pirma ir paskutinė apkarpomos.
Gamybinės funkcijos reikalauja laikotarpio (ym); jokių numatytų mėnesių nėra.
"""
from __future__ import annotations

import csv
import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from openpyxl import load_workbook

from core.models import Finding
from core.period import Period, lt_holidays

WEEK_RANGES = ((1, 4), (5, 11), (12, 18), (19, 25), (26, 31))  # TIK demo (2026-10); gamyboje week_ranges(y, m)
OVERTIME_WEEK_LIMIT = D("12")      # val. per 7 d.
HOURS_TOLERANCE = D("0.01")
NORM_TOLERANCE = D("0.5")

MANAGERS = {  # kreipinys į atsakingą padalinio vadovą
    "Sandėlis": "Sandėlio vadove",
    "Logistika": "Logistikos vadove",
    "Pardavimai": "Pardavimų vadove",
    "Dirbtuvės": "Dirbtuvių vadove",
    "Administracija": "Administracijos vadove",
}


@dataclass(frozen=True)
class TimesheetRow:
    tab_no: str
    name: str
    worked_hours: D                 # įprastos išdirbtos valandos (be viršvalandžių)
    vacation_days: int
    sick_days: int
    overtime_hours: D
    overtime_by_week: tuple[D, ...]
    department: str = ""
    vacation_dates: tuple[date, ...] = ()
    sick_dates: tuple[date, ...] = ()
    day_hours: dict[date, D] = field(default_factory=dict)    # šaltinio valandos pagal dieną (tik > 0)
    day_codes: dict[date, str] = field(default_factory=dict)  # D darbas, A atostogos, L liga, P poilsis
    holiday_hours: D = D(0)         # valandos LT šventėmis ir poilsio (šeštadienio/sekmadienio) dienomis


def _dec(v) -> D:
    return D(str(v)) if v not in (None, "") else D(0)


MAX_DAY_HOURS = D("24")
NON_WORK_MARKS = ("", "P")


def _hours(cell, tab_no: str, label: str, max_value: D | None = None) -> D:
    """Skaitinė reikšmė (skaičius arba tekstas „8“ / „8,5“); neigiamos, per didelės ir neperskaitomos atmetamos."""
    text = str(cell).strip().replace(",", ".")
    try:
        value = D(text)
    except Exception:
        raise ValueError(f"Tabelio nr. {tab_no}, {label}: neperskaitoma reikšmė „{cell}“") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"Tabelio nr. {tab_no}, {label}: netinkamos valandos „{cell}“ (negali būti neigiamos)")
    if max_value is not None and value > max_value:
        raise ValueError(f"Tabelio nr. {tab_no}, {label}: {value} val. viršija {max_value} val. per dieną")
    return value


def week_ranges(year: int, month: int) -> list[tuple[int, int]]:
    """Pirmadienis-sekmadienis savaitės, apkarpytos pagal mėnesį: (pirma diena, paskutinė diena)."""
    last = calendar.monthrange(year, month)[1]
    out, first = [], 1
    for day in range(1, last + 1):
        if date(year, month, day).weekday() == 6:
            out.append((first, day))
            first = day + 1
    if first <= last:
        out.append((first, last))
    return out


def _is_blank(cell) -> bool:
    if isinstance(cell, str):
        cell = cell.strip()
    if cell is None or cell == "" or cell == "P":
        return True
    try:
        return D(str(cell).replace(",", ".")) == 0
    except Exception:
        return False


def _parse_row(values: tuple, cols: dict, ym: tuple[int, int], holidays: set[date] | None = None) -> TimesheetRow:
    year, month = ym
    holidays = lt_holidays(year) if holidays is None else holidays
    last_day = calendar.monthrange(year, month)[1]
    hours, vac, sick = D(0), [], []
    day_hours: dict[date, D] = {}
    day_codes: dict[date, str] = {}
    holiday_hours = D(0)
    tab_no = str(values[cols["tab"]])
    for day, ix in cols.get("extra_days", {}).items():
        if not _is_blank(values[ix]):
            raise ValueError(f"Tabelio nr. {tab_no}: reikšmė {day} d. stulpelyje, o {Period(year, month).label_lt} "
                             f"turi tik {last_day} d.")
    for day, ix in cols["days"].items():
        cell = values[ix]
        if isinstance(cell, str):
            cell = cell.strip()
        d = date(year, month, day)
        if cell == "A":
            vac.append(d)
            day_codes[d] = "A"
        elif cell == "L":
            sick.append(d)
            day_codes[d] = "L"
        elif cell is not None and not isinstance(cell, bool) and cell not in NON_WORK_MARKS:
            h = _hours(cell, tab_no, f"{day} d.", MAX_DAY_HOURS)
            hours += h
            if h > 0:
                day_hours[d] = h
                day_codes[d] = "D"
                if d in holidays or d.weekday() >= 5:
                    holiday_hours += h
            else:
                day_codes[d] = "P"
        else:
            day_codes[d] = "P"
    by_week = tuple(_hours(values[ix], tab_no, "viršvalandžiai") if values[ix] not in (None, "") else D(0)
                    for ix in cols["overtime"])
    n_weeks = len(week_ranges(year, month))
    if any(h > 0 for h in by_week[n_weeks:]):
        raise ValueError(f"Tabelio nr. {tab_no}: viršvalandžiai už {n_weeks + 1}+ savaitę, o "
                         f"{Period(year, month).label_lt} turi {n_weeks} savaites")
    return TimesheetRow(
        tab_no=str(values[cols["tab"]]), name=str(values[cols["name"]]), worked_hours=hours,
        vacation_days=len(vac), sick_days=len(sick), overtime_hours=sum(by_week, D(0)),
        overtime_by_week=by_week, department=str(values[cols["dept"]] or ""),
        vacation_dates=tuple(vac), sick_dates=tuple(sick),
        day_hours=day_hours, day_codes=day_codes, holiday_hours=holiday_hours,
    )


def _columns(header: list, ym: tuple[int, int]) -> dict:
    """Stulpelių žemėlapis su gamybine struktūros patikra: dienų stulpeliai turi būti 1..mėnesio ilgis."""
    for name in ("Tab. Nr.", "Vardas Pavardė", "Padalinys"):
        if name not in header:
            raise ValueError(f"Tabelyje nėra stulpelio „{name}“")
    last_day = calendar.monthrange(*ym)[1]
    numbered = {int(h): i for i, h in enumerate(header) if str(h).strip().isdigit()}
    days = {d: i for d, i in numbered.items() if 1 <= d <= last_day}
    missing = [d for d in range(1, last_day + 1) if d not in days]
    if missing:
        raise ValueError(f"Tabelyje trūksta dienų stulpelių ({Period(*ym).label_lt} turi {last_day} d.): "
                         + ", ".join(map(str, missing)))
    overtime = [i for i, h in enumerate(header) if str(h).startswith("Viršvalandžiai")]
    return {"tab": header.index("Tab. Nr."), "name": header.index("Vardas Pavardė"),
            "dept": header.index("Padalinys"), "days": days, "overtime": overtime,
            "extra_days": {d: i for d, i in numbered.items() if d not in days}}


def load_timesheet(path: str | Path, ym: tuple[int, int]) -> list[TimesheetRow]:
    """Tabelio įkėlimas; `ym` = (metai, mėnuo) privalomas, nes dienos ir savaitės priklauso nuo mėnesio."""
    ws = load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    if not rows or not any(c not in (None, "") for c in rows[0]):
        raise ValueError("Tabelis tuščias: nėra antraštės eilutės")
    cols = _columns([("" if h is None else h) for h in rows[0]], ym)
    holidays = lt_holidays(ym[0])
    parsed = [_parse_row(v, cols, ym, holidays) for v in rows[1:] if v[cols["tab"]]]
    if not parsed:
        raise ValueError("Tabelyje nėra nė vienos darbuotojo eilutės")
    seen: dict[str, str] = {}
    for r in parsed:
        if r.tab_no in seen:
            raise ValueError(f"Tabelyje pasikartoja tabelio numeris {r.tab_no} ({seen[r.tab_no]}; {r.name})")
        seen[r.tab_no] = r.name
    return parsed


def _load_rivile(path: str | Path) -> dict[str, tuple[D, int, int]]:
    ws = load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    ix = {name: rows[0].index(name) for name in ("Tab. Nr.", "Dirbta val.", "Atostogų d.", "Ligos d.")}
    return {str(v[ix["Tab. Nr."]]): (_dec(v[ix["Dirbta val."]]), int(v[ix["Atostogų d."]] or 0),
                                     int(v[ix["Ligos d."]] or 0)) for v in rows[1:] if v[ix["Tab. Nr."]]}


def _load_ranges(path: str | Path) -> dict[str, list[tuple[date, date]]]:
    out: dict[str, list[tuple[date, date]]] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            out.setdefault(rec["tab_no"], []).append(
                (date.fromisoformat(rec["from"]), date.fromisoformat(rec["to"])))
    return out


def _md(d: date) -> str:
    return f"{d:%m-%d}"


def _span(dates: list[date]) -> str:
    return f"{_md(min(dates))}–{_md(max(dates))}"


def _who(r: TimesheetRow) -> str:
    return f"{r.tab_no} {r.name}"


def _mgr(r: TimesheetRow) -> str:
    return MANAGERS.get(r.department, "Padalinio vadove")


def _finding(kind: str, severity: str, title: str, detail: str, r: TimesheetRow, action: str) -> Finding:
    return Finding("timesheet", severity, title, detail, ref=r.tab_no, suggested_action=action, extra={"kind": kind})


def _check_overtime(r: TimesheetRow, ym: tuple[int, int]) -> list[Finding]:
    out = []
    weeks = week_ranges(*ym)
    y, m = ym
    for week, hours in enumerate(r.overtime_by_week):
        if hours > OVERTIME_WEEK_LIMIT and week < len(weeks):
            a, b = weeks[week]
            span = f"{_md(date(y, m, a))}–{_md(date(y, m, b))}"
            out.append(_finding(
                "overtime_over_limit", "error", "Viršvalandžiai virš ribos",
                f"{_who(r)}: {hours} val. viršvalandžių {week + 1} sav. ({span}), riba {OVERTIME_WEEK_LIMIT} val. per 7 d.",
                r, f"{_mgr(r)}, ar {_who(r)} {span} savaitę dirbo {hours} val. viršvalandžių? Riba 12 val. per 7 d. "
                   f"Ar yra darbuotojo sutikimas ir įsakymas, ar tabelį reikia taisyti?"))
    return out


def _uncovered(dates: tuple[date, ...], ranges: list[tuple[date, date]]) -> list[date]:
    return [d for d in dates if not any(a <= d <= b for a, b in ranges)]


def _check_vacation(r: TimesheetRow, requests: list[tuple[date, date]]) -> list[Finding]:
    missing = _uncovered(r.vacation_dates, requests)
    if not missing:
        return []
    span = _span(missing)
    return [_finding(
        "vacation_no_request", "warning", "Atostogos be prašymo",
        f"{_who(r)}: {len(missing)} d. atostogų (A) {span}, prašymo nėra.", r,
        f"{_mgr(r)}, ar {_who(r)} atostogos {span} suderintos? Prašymo sistemoje nėra.")]


def _check_sick(r: TimesheetRow, certificates: list[tuple[date, date]]) -> list[Finding]:
    missing = _uncovered(r.sick_dates, certificates)
    if not missing:
        return []
    span = _span(missing)
    return [_finding(
        "sick_no_certificate", "warning", "Liga be nedarbingumo pažymėjimo",
        f"{_who(r)}: {len(missing)} d. ligos (L) {span}, Sodros nedarbingumo pažymėjimo nėra.", r,
        f"{_mgr(r)}, ar {_who(r)} sirgo {span}? Sodros nedarbingumo pažymėjimo nėra, "
        f"ar galite pateikti pažymėjimą arba patikslinti tabelį?")]


def _check_zero(r: TimesheetRow) -> list[Finding]:
    if r.worked_hours + r.vacation_days + r.sick_days + r.overtime_hours > 0:
        return []
    return [_finding(
        "zero_hours", "error", "Nulis valandų per mėnesį",
        f"{_who(r)}: 0 val. visą mėnesį.", r,
        f"{_mgr(r)}, {_who(r)} šį mėnesį neturi nė vienos darbo valandos. Ar darbuotojas dirbo "
        f"(neapmokamos ar vaiko priežiūros atostogos), ar tabelis neužpildytas?")]


def _check_rivile(r: TimesheetRow, riv: tuple[D, int, int] | None) -> list[Finding]:
    if riv is None:
        return [_finding("rivile_missing", "warning", "Darbuotojo nėra Rivilės eksporte",
                         f"{_who(r)} Rivilės valandų eksporte nerastas.", r,
                         f"Apskaitininke, patikrinkite, ar {_who(r)} užvestas Rivilėje.")]
    hours, vac, sick = riv
    if abs(hours - r.worked_hours) <= HOURS_TOLERANCE and (vac, sick) == (r.vacation_days, r.sick_days):
        return []
    return [_finding(
        "rivile_mismatch", "error", "Tabelis nesutampa su Rivile",
        f"{_who(r)}: tabelyje {r.worked_hours} val., {r.vacation_days} atost. d., {r.sick_days} ligos d.; "
        f"Rivilėje {hours} val., {vac} atost. d., {sick} ligos d.", r,
        f"{_mgr(r)}, {_who(r)}: tabelyje {r.worked_hours} val., Rivilėje {hours} val. "
        f"Kuris skaičius teisingas?")]


def _check_norm(r: TimesheetRow, flagged: bool, norms: dict[str, D] | None, workdays: int) -> list[Finding]:
    """Normų patikra; be `norms` praleidžiama (jokių numatytų darbuotojų)."""
    norm = norms.get(r.tab_no) if norms else None
    if norm is None or flagged:
        return []
    day_hours = norm / workdays
    covered = r.worked_hours + (r.vacation_days + r.sick_days) * day_hours
    if abs(covered - norm) <= NORM_TOLERANCE:
        return []
    return [_finding("norm_deviation", "info", "Valandos nesutampa su norma",
                     f"{_who(r)}: {covered} val. (įsk. A ir L), norma {norm} val.", r,
                     f"{_mgr(r)}, ar {_who(r)} neišdirbo {norm - covered} val. dėl pateisinamos priežasties?")]


def check(timesheet_path, rivile_path=None, sodra_csv=None, requests_csv=None, *,
          ym: tuple[int, int], norms: dict[str, D] | None = None,
          workdays: int | None = None) -> tuple[list[TimesheetRow], list[Finding]]:
    """Rivilės valandų, Sodros pažymų ir atostogų prašymų failai neprivalomi: jų nėra, tos patikros praleidžiamos."""
    rows = load_timesheet(timesheet_path, ym)
    if workdays is None:
        workdays = Period(*ym).workdays()
    rivile = _load_rivile(rivile_path) if rivile_path else None
    certificates = _load_ranges(sodra_csv) if sodra_csv else None
    requests = _load_ranges(requests_csv) if requests_csv else None
    findings: list[Finding] = []
    for r in rows:
        mine = _check_overtime(r, ym)
        if requests is not None:
            mine += _check_vacation(r, requests.get(r.tab_no, []))
        if certificates is not None:
            mine += _check_sick(r, certificates.get(r.tab_no, []))
        mine += _check_zero(r)
        if rivile is not None:
            mine += _check_rivile(r, rivile.get(r.tab_no))
        findings += mine + _check_norm(r, bool(mine), norms, workdays)
    return rows, findings
