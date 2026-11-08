from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from openpyxl import Workbook, load_workbook

import calendar
import dataclasses

from core.models import Finding
from core.period import lt_holidays
from core.rivile_payroll import (COLUMNS, LINES_SHEET, OPERATIONS_SHEET, RivilePayRow, compare, parse_number,
                                 read_rivile_payroll_xlsx, timesheet_to_rivile_xlsx,
                                 timesheet_to_rivile_xlsx_parts)
from core.timesheet import TimesheetRow


def _row(tab="D001", vac=(), sick=(), day_hours=None, ym=(2026, 10), overtime="0"):
    """Pagal nutylėjimą 8 val. kiekvieną darbo dieną (be LT švenčių), išskyrus A/L dienas."""
    if day_hours is None:
        holidays = lt_holidays(ym[0])
        off = set(vac) | set(sick)
        day_hours = {d: D(8) for d in (date(*ym, n) for n in range(1, calendar.monthrange(*ym)[1] + 1))
                     if d.weekday() < 5 and d not in holidays and d not in off}
    return TimesheetRow(tab_no=tab, name="Jonas Jonaitis", worked_hours=sum(day_hours.values(), D(0)),
                        vacation_days=len(vac), sick_days=len(sick), overtime_hours=D(overtime),
                        overtime_by_week=(D(overtime),) + (D(0),) * 4,
                        vacation_dates=tuple(vac), sick_dates=tuple(sick), day_hours=day_hours)


OCT = {"year": 2026, "month": 10}
NOV = {"year": 2026, "month": 11}


def _sheet(path, name):
    ws = load_workbook(path)[name]
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


def test_sheets_columns_and_refids(tmp_path):
    out = timesheet_to_rivile_xlsx([_row("D001"), _row("D002")], "2026-10", {"D001": "E1", "D002": "E2"},
                                   tmp_path / "t.xlsx", **OCT)
    wb = load_workbook(out)
    assert wb.sheetnames == [OPERATIONS_SHEET, LINES_SHEET, "Info"]
    head, ops = _sheet(out, OPERATIONS_SHEET)
    assert head[:3] == ("##refId##", "EmployeeCode", "CalendarPeriodCode")
    assert [o[1] for o in ops] == ["E1", "E2"] and ops[0][2] == "2026-10"
    lhead, lines = _sheet(out, LINES_SHEET)
    assert lhead[:5] == tuple(COLUMNS[k] for k in ("ref", "ts_code", "date_from", "date_to", "days"))
    assert {o[0] for o in ops} == {ln[0] for ln in lines}
    assert ops[0][5].date() == date(2026, 10, 31)


def test_ranges_grouped_by_day_type(tmp_path):
    vac = [date(2026, 10, d) for d in (5, 6, 7)]
    sick = [date(2026, 10, 14)]
    out = timesheet_to_rivile_xlsx([_row(vac=vac, sick=sick)], "P", {"D001": "E1"}, tmp_path / "t.xlsx", **OCT)
    _, lines = _sheet(out, LINES_SHEET)
    kinds = [(ln[1], ln[2].date().day, ln[3].date().day, ln[4]) for ln in lines]
    assert ("A", 5, 7, 3) in kinds and ("L", 14, 14, 1) in kinds
    assert ("DD", 1, 1, 1) in kinds and ("DD", 2, 2, 1) in kinds     # darbo dienos po eilutę
    assert ("P", 3, 4, 2) in kinds            # savaitgalis
    assert sum(k[3] for k in kinds) == 31
    work_hours = sum(ln[5] for ln in lines if ln[1] == "DD")
    assert work_hours == pytest.approx(18 * 8)       # 22 darbo d. - 3 A - 1 L


def test_custom_codes_and_day_hours(tmp_path):
    hours = {date(2026, 10, 1): D("8"), date(2026, 10, 3): D("4")}   # 3 d. = šeštadienis, šaltinis jį nurodo
    out = timesheet_to_rivile_xlsx([_row(day_hours=hours)], "P", {"D001": "E1"}, tmp_path / "t.xlsx",
                                   day_types={"work": "X"}, **OCT)
    _, lines = _sheet(out, LINES_SHEET)
    work = [ln for ln in lines if ln[1] == "X"]
    assert [(w[2].date().day, w[5]) for w in work] == [(1, 8), (3, 4)]


def test_missing_employee_code_lists_tab_numbers(tmp_path):
    with pytest.raises(ValueError, match="D002.*D003"):
        timesheet_to_rivile_xlsx([_row("D001"), _row("D002"), _row("D003")], "P", {"D001": "E1"},
                                 tmp_path / "t.xlsx", **OCT)
    assert not (tmp_path / "t.xlsx").exists()


def test_file_split_at_row_limit(tmp_path):
    rows = [_row(f"D{i:03d}") for i in range(1, 5)]
    codes = {r.tab_no: f"E{r.tab_no}" for r in rows}
    parts = timesheet_to_rivile_xlsx_parts(rows, "P", codes, tmp_path / "t.xlsx", max_rows=60, **OCT)
    assert [p.name for p in parts] == ["t_1.xlsx", "t_2.xlsx"]
    counts = [len(_sheet(p, OPERATIONS_SHEET)[1]) for p in parts]
    assert sum(counts) == 4 and all(len(_sheet(p, LINES_SHEET)[1]) <= 60 for p in parts)
    assert timesheet_to_rivile_xlsx(rows, "P", codes, tmp_path / "u.xlsx", max_rows=60, **OCT).name == "u_1.xlsx"


def _ops(out):
    head, ops = _sheet(out, OPERATIONS_SHEET)
    return dict(zip(head, ops[0]))


def test_november_plan_uses_lt_calendar_20_days_and_no_work_on_nov_2(tmp_path):
    """S6: lapkritis = 20 darbo d. (2 d. šventė); eksportas nededa darbo į 2 d., jei šaltinyje jo nėra."""
    row = _row(ym=(2026, 11))
    out = timesheet_to_rivile_xlsx([row], "2026-11", {"D001": "E1"}, tmp_path / "t.xlsx", **NOV)
    assert _ops(out)["PlannedWorkingDays"] == 20
    _, lines = _sheet(out, LINES_SHEET)
    work_days = {(ln[2].date() + timedelta(days=i)).day
                 for ln in lines if ln[1] == "DD" for i in range(ln[4])}
    assert 2 not in work_days and len(work_days) == 20 and sum(ln[5] for ln in lines if ln[1] == "DD") == 160


def test_december_plan_excludes_24_26(tmp_path):
    out = timesheet_to_rivile_xlsx([_row(ym=(2026, 12))], "2026-12", {"D001": "E1"}, tmp_path / "t.xlsx",
                                   year=2026, month=12)
    assert _ops(out)["PlannedWorkingDays"] == 21


def test_source_per_day_hours_kept_not_redistributed(tmp_path):
    """S6: pamainos (4 val. ir 12 val.) lieka kaip šaltinyje, ne po lygiai."""
    hours = {date(2026, 11, 3): D("4"), date(2026, 11, 4): D("12")}
    out = timesheet_to_rivile_xlsx([_row(day_hours=hours, ym=(2026, 11))], "P", {"D001": "E1"},
                                   tmp_path / "t.xlsx", **NOV)
    _, lines = _sheet(out, LINES_SHEET)
    work = [ln for ln in lines if ln[1] == "DD"]
    assert [(w[2].date().day, w[4], w[5]) for w in work] == [(3, 1, 4), (4, 1, 12)]


def test_row_without_day_hours_cannot_be_exported(tmp_path):
    row = TimesheetRow("D001", "X", D(160), 0, 0, D(0), (D(0),) * 5)
    with pytest.raises(ValueError, match="nėra dienų valandų"):
        timesheet_to_rivile_xlsx([row], "P", {"D001": "E1"}, tmp_path / "t.xlsx", **OCT)


def test_overtime_is_not_silently_dropped_goes_to_separate_file(tmp_path):
    from core.rivile_payroll import timesheet_to_rivile_xlsx_parts
    parts = timesheet_to_rivile_xlsx_parts([_row("D001"), _row("D004", overtime="4")], "P",
                                           {"D001": "E1", "D004": "E4"}, tmp_path / "t.xlsx", year=2026, month=10)
    assert parts[-1].name == "rivile_virsvalandziai_P.xlsx"
    ws = load_workbook(parts[-1])["Virsvalandziai"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows == [("D004", "E4", rows[0][2], "1 sav.", 4)]

def test_year_and_month_are_required(tmp_path):
    with pytest.raises(TypeError):
        timesheet_to_rivile_xlsx([_row()], "P", {"D001": "E1"}, tmp_path / "t.xlsx")


@pytest.mark.parametrize("raw,expected", [("1 234,56", "1234.56"), ("1.234,56", "1234.56"), ("12,5", "12.5"),
                                          ("1\xa0234,56", "1234.56"), (None, "0"), (3.5, "3.5"), ("-", "0")])
def test_parse_number(raw, expected):
    assert parse_number(raw) == D(expected)


def _payroll_file(path, header=None, rows=None):
    wb = Workbook()
    ws = wb.active
    ws.append(["Algalapis"])
    ws.append(header or ["Tabelio Nr.", "Priskaičiuota", "NPD", "GPM", "VSD/PSD darbuotojo",
                         "Darbdavio įmokos", "Išmokėti"])
    for r in rows or [["D001", "1 234,56", "747,00", "100,10", "158,00", "20,00", "976,46"],
                      ["D002", 2000, 0, 300, 250.5, 33, 1449.5]]:
        ws.append(r)
    wb.save(path)
    return path


def test_read_lt_number_formats(tmp_path):
    rows = read_rivile_payroll_xlsx(_payroll_file(tmp_path / "p.xlsx"))
    assert rows[0] == RivilePayRow("D001", D("1234.56"), D("747.00"), D("100.10"), D("158.00"),
                                   D("20.00"), D("976.46"))
    assert rows[1].gross == D("2000.00") and rows[1].sodra_employee == D("250.50")


def test_read_custom_mapping_and_unknown_headers(tmp_path):
    header = ["Kodas", "Bruto", "NPD", "GPM", "Sodra", "Darbdavys", "Neto"]
    p = _payroll_file(tmp_path / "p.xlsx", header, [["E1", "10,00", "1", "2", "3", "4", "5"]])
    mapping = {"id": "Kodas", "gross": "Bruto", "sodra_employee": "Sodra", "sodra_employer": "Darbdavys",
               "net": "Neto"}
    assert read_rivile_payroll_xlsx(p, mapping)[0].net == D("5.00")
    with pytest.raises(ValueError, match="Rastos antraštės.*Bruto"):
        read_rivile_payroll_xlsx(p)


def _act(key="D001", **kw):
    base = dict(gross=D("100"), npd=D("10"), gpm=D("5"), sodra_employee=D("6"), sodra_employer=D("2"),
                net=D("80"))
    return RivilePayRow(key, **{**base, **kw})


def _exp(**kw):
    return {**dict(gross=D("100"), npd=D("10"), gpm=D("5"), sodra_employee=D("6"), sodra_employer=D("2"),
                   net=D("80")), **kw}


def test_compare_exact_and_within_tolerance():
    assert compare({"D001": _exp()}, [_act()]) == []
    assert compare({"D001": _exp(gpm=D("5.01"))}, [_act()]) == []


def test_compare_diff_reports_field_and_totals():
    out = compare({"D001": _exp(gpm=D("5.50"))}, [_act()])
    kinds = {f.extra["kind"] for f in out}
    assert kinds == {"diff_gpm", "total_gpm"}
    diff = next(f for f in out if f.extra["kind"] == "diff_gpm")
    assert isinstance(diff, Finding) and diff.module == "payroll" and diff.ref == "D001"
    assert "5.50" in diff.detail and "5.00" in diff.detail


def test_compare_missing_both_sides():
    out = compare({"D001": _exp(), "D002": _exp()}, [_act("D001"), _act("D003")])
    by_kind = {f.extra["kind"]: f for f in out}
    assert by_kind["missing_in_rivile"].ref == "D002" and by_kind["missing_in_rivile"].severity == "error"
    assert by_kind["missing_in_ours"].ref == "D003" and by_kind["missing_in_ours"].severity == "warning"


def test_overtime_list_text_is_not_a_formula(tmp_path):
    """N4: '=1+1' tabelio nr., kode ar varde lieka tekstu."""
    row = _row("=1+1", overtime="4")
    row = dataclasses.replace(row, name="=2+2")
    parts = timesheet_to_rivile_xlsx_parts([row], "P", {"=1+1": "=3+3"}, tmp_path / "t.xlsx", **OCT)
    ws = load_workbook(parts[-1])["Virsvalandziai"]
    cells = [c for c in ws[2][:3]]
    assert [c.value for c in cells] == ["=1+1", "=3+3", "=2+2"] and all(c.data_type == "s" for c in cells)


def test_adjacent_work_days_not_merged_even_in_long_streak(tmp_path):
    out = timesheet_to_rivile_xlsx([_row()], "P", {"D001": "E1"}, tmp_path / "t.xlsx", **OCT)
    _, lines = _sheet(out, LINES_SHEET)
    assert all(ln[4] == 1 for ln in lines if ln[1] == "DD") and len([ln for ln in lines if ln[1] == "DD"]) == 22
