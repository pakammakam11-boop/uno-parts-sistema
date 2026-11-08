"""Rivilė ERP darbo užmokestis: tabelio XLSX importas ir DU eksporto palyginimas.

1) timesheet_to_rivile_xlsx: tabelio importo failas (research: guide.rivile.cloud/modules/payrolls/
   working_time/task_0003): lapai 'Timesheet_operations' ir 'Lines'. Visi stulpelių pavadinimai yra
   COLUMNS žodyne, o laiko tipų kodai DEFAULT_DAY_TYPES: tai NUMANYTOSIOS reikšmės, jas būtina suderinti
   su UNO Rivilės šablonu ir kodų kartoteka (B2), keičiame tik čia arba perduodame day_types.
2) read_rivile_payroll_xlsx: Rivilės DU ataskaitos nuskaitymas su konfigūruojamu antraščių žemėlapiu.
3) compare: mūsų skaičiavimo ir Rivilės DU skirtumai (Finding, modulis 'payroll').

Eksportas naudoja šaltinio dienų valandas (TimesheetRow.day_hours / day_codes); jų neišvedame iš mėnesio
sumos. Planuojamos darbo dienos imamos iš LT kalendoriaus (core.period: šventės, savaitgaliai).
Viršvalandžiai NEeksportuojami: jų dienos tipas Rivilės Lines lape nesuderintas, todėl eksportas
su viršvalandžiais nutraukiamas (ValueError), kad jie nebūtų tyliai prarasti.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from core.models import Finding, money
from core.period import Period, lt_holidays
from core.rivile_export import _put, ref_id
from core.timesheet import TimesheetRow

COLUMNS = {
    "ref": "##refId##",
    "employee": "EmployeeCode",
    "period": "CalendarPeriodCode",
    "agreement": "EmployeeAgreementCode",
    "doc_no": "DocumentNo",
    "op_date": "OpDate",
    "planned_days": "PlannedWorkingDays",
    "planned_hours": "PlannedWorkingHours",
    "notes": "Notes",
    "ts_code": "timesheetCode",
    "date_from": "dateFrom",
    "date_to": "dateTo",
    "days": "days",
    "hours": "hours",
}
OPERATION_KEYS = ["ref", "employee", "period", "agreement", "doc_no", "op_date",
                  "planned_days", "planned_hours", "notes"]
LINE_KEYS = ["ref", "ts_code", "date_from", "date_to", "days", "hours", "notes"]
OPERATIONS_SHEET, LINES_SHEET = "Timesheet_operations", "Lines"

# NUMANYTI kodai: suderinti su UNO Rivilės laiko tipų kartoteka.
DEFAULT_DAY_TYPES = {"work": "DD", "vacation": "A", "sick": "L", "rest": "P"}
MAX_ROWS, MAX_BYTES = 10_000, 5 * 1024 * 1024

INFO_ROWS = [
    "Tabelio importas Rivilėje: lapas 'Timesheet_operations' (viena eilutė darbuotojui) ir lapas 'Lines'.",
    "##refId## sieja eilutę lape 'Lines' su darbuotojo eilute lape 'Timesheet_operations'.",
    "EmployeeCode: darbuotojo kodas Rivilėje; CalendarPeriodCode: kalendoriaus laikotarpio kodas.",
    "DocumentNo ir OpDate: tabelio dokumento numeris ir data (laikotarpio paskutinė diena).",
    "PlannedWorkingDays: darbo dienų skaičius laikotarpyje pagal LT kalendorių (be savaitgalių ir švenčių).",
    "Viršvalandžiai šiuo failu neperduodami (jų laiko tipas Rivilėje nesuderintas): eksportas su jais nutraukiamas.",
    "Lines: timesheetCode (laiko tipas), dateFrom/dateTo, days (kalendorinių dienų skaičius), hours (tik darbo "
    "laikui). Darbo laikas rašomas po eilutę kiekvienai dienai (kiekvienos dienos valandos), o atostogos, liga ir "
    "poilsis grupuojami į nepertraukiamus intervalus.",
    "Numanyti laiko tipų kodai: DD darbo diena, A atostogos, L liga, P poilsis. Tai NUMANYTOS reikšmės: "
    "būtina sutikrinti su UNO Rivilės atsisiųstu šablonu ir kodų kartoteka.",
    "Stulpelius ir lapų pavadinimus sutikrinti su atsisiųstu šablonu (Darbo užmokestis → Darbo laiko apskaita "
    "→ Importuoti → Atsisiųsti XLSX šabloną).",
    "Importo ribos: iki 10 000 eilučių ir 5 MB; didesnis kiekis dalijamas į kelis failus (_1, _2 ...).",
]


# ---------- 1. Tabelio eksportas ----------

def _month_days(year: int, month: int) -> list[date]:
    return [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def _source_hours(row: TimesheetRow) -> dict[date, D]:
    """Šaltinio dienų valandos; jei eilutė jų neturi, eksportas negalimas (jokio paskirstymo iš sumos)."""
    if not row.day_hours and row.worked_hours > 0:
        raise ValueError(f"Tabelio nr. {row.tab_no}: nėra dienų valandų, eksportuoti negalima")
    return row.day_hours


def write_overtime_list(rows: list[TimesheetRow], employee_codes: dict[str, str], path) -> Path | None:
    """Viršvalandžių dienos tipas Rivilėje dar nesuderintas, todėl jie neeina į tabelio importą.

    Kad niekas nedingtų tyliai, jie išrašomi atskirame faile rankiniam suvedimui Rivilėje.
    Grąžina None, jei viršvalandžių nėra.
    """
    with_overtime = [r for r in rows if r.overtime_hours > 0]
    if not with_overtime:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "Virsvalandziai"
    _put(ws, ["Tab. Nr.", "Rivilės kodas", "Darbuotojas", "Savaitė", "Valandos"])
    for r in with_overtime:
        for week, hours in enumerate(r.overtime_by_week, start=1):
            if hours > 0:
                _put(ws, [r.tab_no, employee_codes.get(r.tab_no, ""), r.name, f"{week} sav.", hours])
    info = wb.create_sheet("Info")
    _put(info, ["Viršvalandžiai neįtraukti į tabelio importo failą: jų dienos tipas Rivilėje dar nesuderintas."])
    _put(info, ["Suveskite juos Rivilėje rankiniu būdu arba suderinkite dienos tipą (B fazė)."])
    out = Path(path)
    wb.save(out)
    return out


def _classify(row: TimesheetRow, days: list[date], hours: dict[date, D]) -> list[tuple[date, str, D]]:
    out = []
    for d in days:
        if d in row.vacation_dates:
            out.append((d, "vacation", D(0)))
        elif d in row.sick_dates:
            out.append((d, "sick", D(0)))
        elif hours.get(d, D(0)) > 0:
            out.append((d, "work", hours[d]))
        else:
            out.append((d, "rest", D(0)))
    return out


def _group_ranges(daily: list[tuple[date, str, D]]) -> list[tuple[str, date, date, int, D]]:
    """Nepertraukiamos dienos -> (rūšis, nuo, iki, dienų, valandų suma).

    Grupuojami tik tipai be valandų (atostogos, liga, poilsis); darbo diena visada atskira eilutė,
    kad 4 ir 12 val. pamainos nebūtų sulietos.
    """
    ranges: list[list] = []
    for d, kind, h in daily:
        last = ranges[-1] if ranges else None
        if last and kind != "work" and last[0] == kind and last[2] + timedelta(days=1) == d:
            last[2], last[3], last[4] = d, last[3] + 1, last[4] + h
        else:
            ranges.append([kind, d, d, 1, h])
    return [tuple(r) for r in ranges]


def _check_codes(rows: list[TimesheetRow], employee_codes: dict) -> None:
    missing = sorted(r.tab_no for r in rows if not employee_codes.get(r.tab_no))
    if missing:
        raise ValueError("Trūksta Rivilės darbuotojo kodo tabelio numeriams: " + ", ".join(missing))


def _employee_rows(row: TimesheetRow, n: int, code: str, period_code: str, codes: dict,
                   days: list[date], planned: int) -> tuple[list, list[list]]:
    hours = _source_hours(row)
    ref = ref_id(n)
    op = {"ref": ref, "employee": code, "period": period_code, "doc_no": f"TAB-{period_code}-{row.tab_no}",
          "op_date": days[-1], "planned_days": planned}
    lines = [{"ref": ref, "ts_code": codes[kind], "date_from": a, "date_to": b, "days": n_days,
              "hours": h if kind == "work" else ""}
             for kind, a, b, n_days, h in _group_ranges(_classify(row, days, hours))]
    return [op.get(k, "") for k in OPERATION_KEYS], [[ln.get(k, "") for k in LINE_KEYS] for ln in lines]


def _chunk(items: list[tuple[list, list[list]]], max_rows: int) -> list[list[tuple[list, list[list]]]]:
    chunks: list[list] = [[]]
    used = 0
    for op, lines in items:
        if len(lines) > max_rows:
            raise ValueError(f"Vieno darbuotojo eilučių ({len(lines)}) daugiau nei riba {max_rows}")
        if used + len(lines) > max_rows or len(chunks[-1]) >= max_rows:
            chunks.append([])
            used = 0
        chunks[-1].append((op, lines))
        used += len(lines)
    return chunks


def _write_sheet(wb: Workbook, title: str, keys: list[str], rows: Iterable[list]) -> None:
    ws = wb.create_sheet(title)
    _put(ws, [COLUMNS[k] for k in keys])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        _put(ws, row)
    for idx, key in enumerate(keys, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = 20
        if key in ("op_date", "date_from", "date_to"):
            for col in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                for c in col:
                    c.number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"


def _write_chunk(chunk: list, path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, OPERATIONS_SHEET, OPERATION_KEYS, [op for op, _ in chunk])
    _write_sheet(wb, LINES_SHEET, LINE_KEYS, [ln for _, lines in chunk for ln in lines])
    info = wb.create_sheet("Info")
    _put(info, ["Pastaba"])
    info["A1"].font = Font(bold=True)
    for text in INFO_ROWS:
        _put(info, [text])
    info.column_dimensions["A"].width = 130
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def timesheet_to_rivile_xlsx_parts(rows: list[TimesheetRow], period_code: str, employee_codes: dict[str, str],
                                   path, day_types: dict[str, str] | None = None, *,
                                   year: int, month: int,
                                   max_rows: int = MAX_ROWS, max_bytes: int = MAX_BYTES) -> list[Path]:
    """Visi failai: vienas, jei telpa į ribas; kitaip path_1.xlsx, path_2.xlsx ... (pagal darbuotojus).

    `year`/`month` privalomi. Viršvalandžiai į importą neina: jie išrašomi atskirame faile (paskutinis sąraše).
    """
    _check_codes(rows, employee_codes)
    codes = {**DEFAULT_DAY_TYPES, **(day_types or {})}
    days = _month_days(year, month)
    planned = Period(year, month).workdays(lt_holidays(year))
    items = [_employee_rows(r, i, employee_codes[r.tab_no], period_code, codes, days, planned)
             for i, r in enumerate(rows, start=1)]
    chunks = _chunk(items, max_rows)
    path = Path(path)
    paths = [path] if len(chunks) == 1 else [path.with_name(f"{path.stem}_{i}{path.suffix}")
                                              for i in range(1, len(chunks) + 1)]
    written = [_write_chunk(c, p) for c, p in zip(chunks, paths)]
    for p in written:
        if p.stat().st_size > max_bytes:
            raise ValueError(f"{p.name} viršija {max_bytes} B ribą; sumažinkite max_rows")
    overtime = write_overtime_list(rows, employee_codes, path.with_name(f"rivile_virsvalandziai_{period_code}.xlsx"))
    return written + ([overtime] if overtime else [])


def timesheet_to_rivile_xlsx(rows, period_code, employee_codes, path, day_types=None, **kwargs) -> Path:
    """Tabelio importo XLSX; grąžina pirmą failą (jei failų keli, visus duoda ..._parts)."""
    return timesheet_to_rivile_xlsx_parts(rows, period_code, employee_codes, path, day_types, **kwargs)[0]


# ---------- 2. Rivilės DU eksporto nuskaitymas ----------

PAY_FIELDS = ("gross", "npd", "gpm", "sodra_employee", "sodra_employer", "net")
FIELD_LABELS = {"gross": "Priskaičiuota (bruto)", "npd": "NPD", "gpm": "GPM",
                "sodra_employee": "Darbuotojo Sodra (VSD/PSD)", "sodra_employer": "Darbdavio įmokos",
                "net": "Išmokėti (neto)"}
DEFAULT_PAY_MAPPING = {
    "id": "Tabelio Nr.", "gross": "Priskaičiuota", "npd": "NPD", "gpm": "GPM",
    "sodra_employee": "VSD/PSD darbuotojo", "sodra_employer": "Darbdavio įmokos", "net": "Išmokėti",
}
HEADER_SCAN_ROWS = 10


@dataclass(frozen=True)
class RivilePayRow:
    key: str                 # tabelio nr. arba Rivilės darbuotojo kodas
    gross: D
    npd: D
    gpm: D
    sodra_employee: D
    sodra_employer: D
    net: D


def _norm(text) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split()).casefold()


def parse_number(value) -> D:
    """'1 234,56', '1.234,56', '1234.56', skaičius arba tuščia -> Decimal (tuščia = 0)."""
    if value is None or isinstance(value, bool):
        return D(0)
    if isinstance(value, (int, float, D)):
        return D(str(value))
    text = "".join(str(value).split()).replace(" ", "").replace("€", "")
    if text in ("", "-", "–"):
        return D(0)
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    else:
        text = text.replace(",", ".")
    try:
        return D(text)
    except Exception:
        raise ValueError(f"Neperskaitomas skaičius „{value}“") from None


def _find_header(rows: list[tuple], mapping: dict[str, str]) -> tuple[int, dict[str, int]]:
    wanted = {f: _norm(h) for f, h in mapping.items()}
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        names = [_norm(c) for c in row]
        if wanted["id"] in names and all(w in names for w in wanted.values()):
            return i, {f: names.index(w) for f, w in wanted.items()}
    found = [str(c) for row in rows[:HEADER_SCAN_ROWS] for c in row if c not in (None, "")]
    raise ValueError(f"Rivilės DU faile nerastos antraštės pagal žemėlapį {mapping}. Rastos antraštės: {found}")


def read_rivile_payroll_xlsx(path, mapping: dict[str, str] | None = None) -> list[RivilePayRow]:
    maps = {**DEFAULT_PAY_MAPPING, **(mapping or {})}
    rows = list(load_workbook(path, data_only=True).active.iter_rows(values_only=True))
    start, ix = _find_header(rows, maps)
    out: list[RivilePayRow] = []
    seen: set[str] = set()
    for n, row in enumerate(rows[start + 1:], start=start + 2):
        key = str(row[ix["id"]] or "").strip()
        if not key:
            continue
        if key in seen:
            raise ValueError(f"Rivilės DU faile pasikartoja {key} (eilutė {n})")
        seen.add(key)
        try:
            vals = {f: money(parse_number(row[ix[f]])) for f in PAY_FIELDS}
        except ValueError as exc:
            raise ValueError(f"Eilutė {n}, {key}: {exc}") from None
        out.append(RivilePayRow(key=key, **vals))
    return out


# ---------- 3. Palyginimas ----------

def _finding(severity: str, title: str, detail: str, ref: str, kind: str) -> Finding:
    return Finding("payroll", severity, title, detail, ref=ref,
                   suggested_action="Apskaitininke, patikrinkite DU skaičiavimą Rivilėje.",
                   extra={"kind": kind})


def _compare_one(key: str, exp: dict, act: RivilePayRow, tol: D) -> list[Finding]:
    out = []
    for f in PAY_FIELDS:
        if f not in exp:
            continue
        ours, theirs = money(exp[f]), money(getattr(act, f))
        if abs(ours - theirs) > tol:
            out.append(_finding("error", f"DU skirtumas: {FIELD_LABELS[f]}",
                                f"{key}: mūsų {ours} €, Rivilėje {theirs} € (skirtumas {theirs - ours} €).",
                                key, f"diff_{f}"))
    return out


def _compare_totals(expected: dict, actual: dict[str, RivilePayRow], tol: D) -> list[Finding]:
    out = []
    for f in PAY_FIELDS:
        ours_vals = [money(e[f]) for e in expected.values() if f in e]
        if not ours_vals:
            continue
        ours, theirs = sum(ours_vals, D(0)), sum((money(getattr(a, f)) for a in actual.values()), D(0))
        if abs(ours - theirs) > tol:
            out.append(_finding("error", f"DU sumų skirtumas: {FIELD_LABELS[f]}",
                                f"Iš viso: mūsų {ours} €, Rivilėje {theirs} € (skirtumas {theirs - ours} €); "
                                f"tikrinti GPM313 / SAM sumas.", "TOTAL", f"total_{f}"))
    return out


def compare(expected: dict[str, dict[str, D]], actual: Iterable[RivilePayRow],
            tolerance: D = D("0.01")) -> list[Finding]:
    by_key = {a.key: a for a in actual}
    findings: list[Finding] = []
    for key, exp in expected.items():
        if key not in by_key:
            findings.append(_finding("error", "Darbuotojo nėra Rivilės DU", f"{key}: Rivilės DU faile nerastas.",
                                     key, "missing_in_rivile"))
            continue
        findings += _compare_one(key, exp, by_key[key], tolerance)
    for key in by_key:
        if key not in expected:
            findings.append(_finding("warning", "Darbuotojo nėra mūsų skaičiavime",
                                     f"{key}: yra Rivilės DU faile, bet nėra mūsų skaičiavime.",
                                     key, "missing_in_ours"))
    return findings + _compare_totals(expected, by_key, tolerance)
