from decimal import Decimal as D

import pytest

from core import world
from core.payroll import run_month
from core.timesheet import check, load_timesheet
from data import gen_timesheet


@pytest.fixture(scope="module")
def files():
    return gen_timesheet.main()


OCT = (2026, 10)


def _check(files):
    return check(files["tabelis"], files["rivile"], files["sodra"], files["requests"], ym=OCT,
                 norms={e.tab_no: e.hours_norm for e in world.EMPLOYEES})


def test_generated_files_exist(files):
    assert set(files) == {"tabelis", "rivile", "sodra", "requests"}
    assert all(p.exists() and p.stat().st_size > 0 for p in files.values())


def test_generation_is_deterministic(files, tmp_path):
    other = gen_timesheet.main(tmp_path)
    for key in ("sodra", "requests"):
        assert files[key].read_text(encoding="utf-8") == other[key].read_text(encoding="utf-8")
    assert load_timesheet(files["tabelis"], OCT) == load_timesheet(other["tabelis"], OCT)


def test_load_timesheet_57_rows(files):
    rows = {r.tab_no: r for r in load_timesheet(files["tabelis"], OCT)}
    assert len(rows) == 57 and set(rows) == {e.tab_no for e in world.EMPLOYEES}
    assert max(rows["D004"].overtime_by_week) == D(14)
    assert rows["D017"].vacation_days == 5
    assert rows["D029"].sick_days == 3
    assert rows["D041"].worked_hours == 0
    assert rows["D050"].worked_hours == 176


def test_check_finds_exactly_the_injected_anomalies(files):
    _, findings = _check(files)
    problems = [f for f in findings if f.severity in ("error", "warning")]
    assert {(f.ref, f.extra["kind"]) for f in problems} == {(a.tab_no, a.kind) for a in world.TIMESHEET_ANOMALIES}
    assert len(problems) == 5
    assert all(f.module == "timesheet" for f in findings)


def test_suggested_actions_are_ready_questions(files):
    _, findings = _check(files)
    by_ref = {f.ref: f for f in findings if f.severity != "info"}
    d017 = by_ref["D017"].suggested_action
    assert d017.startswith("Sandėlio vadove") or "vadove" in d017
    assert "D017" in d017 and "Prašymo sistemoje nėra" in d017 and "10-12–10-16" in d017
    assert "168" in by_ref["D050"].suggested_action and "176" in by_ref["D050"].suggested_action
    assert all("?" in f.suggested_action and "vadove" in f.suggested_action for f in by_ref.values())
    assert all(f.ref in f.detail for f in by_ref.values())


def test_no_false_errors_on_normal_employees(files):
    _, findings = _check(files)
    anomaly_tabs = {a.tab_no for a in world.TIMESHEET_ANOMALIES}
    assert not [f for f in findings if f.ref not in anomaly_tabs and f.severity != "info"]


def test_run_month_on_generated_timesheet(files):
    rows = load_timesheet(files["tabelis"], OCT)
    pay, summary = run_month(world.EMPLOYEES, rows)
    assert len(pay) == 57
    assert summary.gross == sum((p.gross for p in pay), D(0))
    assert summary.net == sum((p.net for p in pay), D(0))
    zero = next(p for p in pay if p.tab_no == "D041")
    assert zero.gross == 0 and zero.net == 0
    d001 = next(p for p in pay if p.tab_no == "D001")
    assert d001.net == d001.net_agreed


# ---------- gamybinė struktūra ir mėnesio savaitės ----------

NOV, DEC = (2026, 11), (2026, 12)


def _write_sheet(path, ym, rows=(), overtime_cols=5, n_day_cols=None):
    """rows: (tab, vardas, {diena: reikšmė}, [viršvalandžiai pagal savaitę])."""
    from calendar import monthrange

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    n = n_day_cols if n_day_cols is not None else monthrange(*ym)[1]
    ws.append(["Tab. Nr.", "Vardas Pavardė", "Padalinys", *range(1, n + 1),
               *[f"Viršvalandžiai {i} sav." for i in range(1, overtime_cols + 1)]])
    for tab, name, days, ot in rows:
        ot = list(ot) + [None] * (overtime_cols - len(ot))
        ws.append([tab, name, "Sandėlis", *[days.get(d) for d in range(1, n + 1)], *ot])
    wb.save(path)
    return path


def _month_days(ym, hours="8"):
    from datetime import date
    from calendar import monthrange
    return {d: hours for d in range(1, monthrange(*ym)[1] + 1) if date(*ym, d).weekday() < 5}


def test_text_numbers_are_parsed(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", OCT, [("D1", "A B", {1: "8", 2: "8,5", 5: 8}, ["2,5"])])
    r = load_timesheet(p, OCT)[0]
    assert r.worked_hours == D("24.5") and r.overtime_hours == D("2.5")


@pytest.mark.parametrize("bad", ["-8", -8, "abc", 25])
def test_bad_hours_rejected(tmp_path, bad):
    p = _write_sheet(tmp_path / "t.xlsx", OCT, [("D1", "A B", {1: bad}, [])])
    with pytest.raises(ValueError, match="D1"):
        load_timesheet(p, OCT)


def test_duplicate_tab_no_rejected(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", OCT, [("D1", "A B", {1: 8}, []), ("D1", "A B", {1: 0}, [])])
    with pytest.raises(ValueError, match="pasikartoja"):
        load_timesheet(p, OCT)


def test_week_ranges_follow_the_real_month():
    from core.timesheet import WEEK_RANGES, week_ranges
    assert week_ranges(2026, 10) == list(WEEK_RANGES)
    assert week_ranges(2026, 11) == [(1, 1), (2, 8), (9, 15), (16, 22), (23, 29), (30, 30)]
    assert week_ranges(2026, 8)[-1] == (31, 31) and len(week_ranges(2026, 8)) == 6


def test_november_30_day_month_week5_overtime_does_not_crash(tmp_path):
    """S9: anksčiau date(y, m, 31) lapkričiui nutraukdavo visą patikrą."""
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", _month_days(NOV), [0, 0, 0, 0, 13])])
    _, findings = check(p, ym=NOV)
    over = [f for f in findings if f.extra["kind"] == "overtime_over_limit"]
    assert len(over) == 1 and "5 sav." in over[0].detail and "11-23–11-29" in over[0].detail


def test_sixth_week_overtime_with_six_columns_and_without_index_error(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", _month_days(NOV), [0, 0, 0, 0, 0, 13])],
                     overtime_cols=6)
    _, findings = check(p, ym=NOV)
    assert any("6 sav." in f.detail and "11-30–11-30" in f.detail for f in findings)


def test_overtime_in_nonexistent_week_rejected(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", (2027, 2), [("D1", "A B", _month_days((2027, 2)), [0, 0, 0, 0, 5])])
    with pytest.raises(ValueError, match="savait"):
        load_timesheet(p, (2027, 2))


# ---------- S10: struktūros patikra ----------

def test_empty_file_and_no_employees_rejected(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.save(tmp_path / "empty.xlsx")
    with pytest.raises(ValueError, match="tuščias"):
        load_timesheet(tmp_path / "empty.xlsx", NOV)
    p = _write_sheet(tmp_path / "hdr.xlsx", NOV, [])
    with pytest.raises(ValueError, match="nė vienos darbuotojo"):
        load_timesheet(p, NOV)


def test_missing_day_columns_rejected(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", {1: 8}, [])], n_day_cols=3)
    with pytest.raises(ValueError, match="trūksta dienų stulpelių"):
        load_timesheet(p, NOV)


def test_value_in_day_31_of_30_day_month_rejected_but_blank_allowed(tmp_path):
    bad = _write_sheet(tmp_path / "bad.xlsx", NOV, [("D1", "A B", {1: 8, 31: 8}, [])], n_day_cols=31)
    with pytest.raises(ValueError, match="31 d. stulpelyje.*30 d."):
        load_timesheet(bad, NOV)
    ok = _write_sheet(tmp_path / "ok.xlsx", NOV, [("D1", "A B", {1: 8}, [])], n_day_cols=31)
    assert load_timesheet(ok, NOV)[0].worked_hours == 8


# ---------- S6/S7: dienų valandos ir šventinis darbas ----------

def test_row_keeps_per_day_hours_and_codes(tmp_path):
    from datetime import date
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", {3: 8, 4: "A", 5: "L", 6: 0}, [])])
    r = load_timesheet(p, NOV)[0]
    assert r.day_hours == {date(2026, 11, 3): D(8)}
    assert r.day_codes[date(2026, 11, 3)] == "D" and r.day_codes[date(2026, 11, 4)] == "A"
    assert r.day_codes[date(2026, 11, 5)] == "L" and r.day_codes[date(2026, 11, 6)] == "P"
    assert r.holiday_hours == 0


def test_holiday_and_weekend_work_flagged(tmp_path):
    """Lapkričio 2 d. (šventė) ir 7 d. (šeštadienis)."""
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", {2: 8, 3: 8, 7: 4}, [])])
    r = load_timesheet(p, NOV)[0]
    assert r.holiday_hours == 12 and r.worked_hours == 20


def test_dec_24_26_are_holidays(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", DEC, [("D1", "A B", {23: 8, 24: 8, 25: 8}, [])])
    assert load_timesheet(p, DEC)[0].holiday_hours == 16


def test_norm_check_uses_period_workdays_not_october(tmp_path):
    """Lapkritis: 20 darbo dienų (2 d. šventė). 20 d. po 8 val. = 160 val. = norma, nesutapimo nėra."""
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", {d: 8 for d in _month_days(NOV) if d != 2}, [])])
    _, findings = check(p, ym=NOV, norms={"D1": D(160)})
    assert not [f for f in findings if f.extra["kind"] == "norm_deviation"]


def test_check_requires_period(tmp_path):
    p = _write_sheet(tmp_path / "t.xlsx", NOV, [("D1", "A B", {1: 8}, [])])
    with pytest.raises(TypeError):
        check(p)
    with pytest.raises(TypeError):
        load_timesheet(p)
