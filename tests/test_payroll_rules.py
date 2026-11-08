from decimal import Decimal as D

import pytest

from core import rates_2026 as r
from core.models import money
from core.payroll_rules import (EmployeeTerms, MonthIncome, REASON_NPD_SICK, check_overtime_limits,
                                compute_expected, overtime_pay, vdu_daily)
from core.period import Period
from core.timesheet import TimesheetRow

P = Period(2026, 12)   # 21 darbo diena (24-26 d. šventės)


def ts(worked="160", vac=0, sick=0, ot="0", by_week=()):
    return TimesheetRow("D001", "Test", D(worked), vac, sick, D(ot), tuple(D(x) for x in by_week))


def month(salary="1600", days="21", **extra):
    comps = {"salary": D(salary), **{k: D(v) for k, v in extra.items()}}
    return MonthIncome("2026-09", D(days), comps)


def hist(**extra):
    return [month(**extra), month(), month()]


TERMS = EmployeeTerms(net_agreed=D("1000.00"), hours_norm=D("160"))


def test_vdu_includes_supplements_and_bonus():
    res = vdu_daily(hist(overtime="60", bonus="3"), 21)
    assert res.from_history == money(D("4863") / 63)


def test_vdu_excludes_vacation_sick_per_diem_downtime():
    h = hist(vacation_pay="500", sick_pay="300", per_diem="100", downtime="50")
    assert vdu_daily(h, 21).from_history == money(D("4800") / 63)


def test_vdu_floor_mma_over_workdays():
    low = [MonthIncome("m", D("21"), {"salary": D("500")}) for _ in range(3)]
    res = vdu_daily(low, 21)
    assert res.used_floor and res.daily == money(r.MMA / 21)


def test_vdu_needs_three_months_and_known_components():
    with pytest.raises(ValueError):
        vdu_daily([month()], 21)
    with pytest.raises(ValueError):
        vdu_daily(hist(mystery="1"), 21)


def test_overtime_multipliers():
    h = D("10")
    assert overtime_pay(h, "overtime", D(2)) == D("30.00")
    assert overtime_pay(h, "night", D(2)) == D("30.00")
    assert overtime_pay(h, "holiday", D(2)) == D("40.00")
    assert overtime_pay(h, "rest_day", D(2)) == D("40.00")
    assert overtime_pay(h, "overtime_holiday", D(2)) == D("50.00")
    assert overtime_pay(h, "overtime_night", D(2)) == D("40.00")
    with pytest.raises(ValueError):
        overtime_pay(h, "x", D(1))


def test_overtime_limits_week_consent_and_year():
    assert check_overtime_limits("D1", (D(5), D(8))) == []
    assert len(check_overtime_limits("D1", (D(9),))) == 1
    assert check_overtime_limits("D1", (D(11),), consent=True) == []
    assert len(check_overtime_limits("D1", (D(13),), consent=True)) == 1
    year = check_overtime_limits("D1", (D(5),), year_to_date=D(176))
    assert len(year) == 1 and "metin" in year[0].title


def test_sick_pay_gpm_15_no_sodra():
    terms = EmployeeTerms(D("1000.00"), D("160"), npd_applies=False)
    exp = compute_expected(terms, ts(worked="144", sick=2), hist(), P)
    vdu = vdu_daily(hist(), 21).daily
    assert exp.sick_pay == money(vdu * D("0.6206") * 2)
    assert exp.sick_gpm == money(exp.sick_pay * D("0.15"))
    salary_only = compute_expected(terms, ts(worked="144"), None, P)
    assert exp.sodra_employee == salary_only.sodra_employee
    assert exp.sodra_employer == salary_only.sodra_employer
    assert exp.supported


def test_sick_pay_capped_at_two_days_and_rate_validated():
    terms = EmployeeTerms(D("1000.00"), D("160"), npd_applies=False, sick_pay_rate=D("1"))
    exp = compute_expected(terms, ts(worked="120", sick=5), hist(), P)
    assert exp.sick_pay == money(vdu_daily(hist(), 21).daily * 2)
    with pytest.raises(ValueError):
        EmployeeTerms(D("1000"), D("160"), sick_pay_rate=D("0.5"))


def test_npd_with_sick_unsupported():
    exp = compute_expected(TERMS, ts(worked="144", sick=2), hist(), P)
    assert not exp.supported and REASON_NPD_SICK in exp.unsupported_reasons


def test_pension_accumulation_22_5_percent():
    terms = EmployeeTerms(D("1000.00"), D("160"), pension_accumulation=True)
    exp = compute_expected(terms, ts(), None, P)
    assert exp.sodra_employee == money(exp.gross_salary * D("0.225"))


def test_unsupported_cases_flagged():
    assert any("istorijos" in x for x in compute_expected(TERMS, ts(sick=1), None, P).unsupported_reasons)
    assert not compute_expected(EmployeeTerms(D("1000"), D("160"), hired_this_month=True), ts(), None, P).supported
    assert not compute_expected(EmployeeTerms(D("1000"), D("160"), employers_count=2), ts(), None, P).supported
    assert not compute_expected(TERMS, ts(vac=3), None, P).supported
    night = ts()
    object.__setattr__(night, "night_hours", D("4"))
    assert not compute_expected(TERMS, night, None, P).supported


def test_full_month_no_extras_net_equals_agreed():
    for net in ("1000.00", "1500.00", "2400.00"):
        exp = compute_expected(EmployeeTerms(D(net), D("160")), ts(), None, P)
        assert exp.supported and exp.net == D(net)
        assert exp.sick_pay == 0 and exp.gross_overtime == 0


def test_overtime_adds_pay_at_1_5():
    exp = compute_expected(TERMS, ts(ot="4"), None, P)
    hourly_full = compute_expected(TERMS, ts(), None, P).gross_salary / 160
    assert abs(exp.gross_overtime - hourly_full * D("1.5") * 4) <= D("0.02")
    assert exp.net > D("1000.00")


# ---------- S7, S12, S13, ligos dienos ----------

def test_workdays_of_test_periods():
    assert Period(2026, 11).workdays() == 20        # 2 d. šventė
    assert Period(2026, 12).workdays() == 21        # 24-26 d. šventės


def test_holiday_work_marks_employee_unsupported():
    from datetime import date

    from core.payroll_rules import REASON_HOLIDAY_WORK
    row = TimesheetRow("D001", "Test", D("160"), 0, 0, D(0), (), holiday_hours=D(8),
                       day_hours={date(2026, 11, 2): D(8)})
    exp = compute_expected(TERMS, row, None, Period(2026, 11))
    assert not exp.supported and REASON_HOLIDAY_WORK in exp.unsupported_reasons


def test_holiday_work_loaded_from_sheet_reaches_rules(tmp_path):
    from openpyxl import Workbook

    from core.timesheet import load_timesheet
    wb = Workbook()
    ws = wb.active
    ws.append(["Tab. Nr.", "Vardas Pavardė", "Padalinys", *range(1, 31), "Viršvalandžiai 1 sav."])
    ws.append(["D1", "A B", "Sandėlis", None, 8, *[None] * 28, None])      # 2 d. = šventė
    row = load_timesheet(_save(wb, tmp_path), (2026, 11))[0]
    exp = compute_expected(TERMS, row, None, Period(2026, 11))
    assert not exp.supported and any("švenčių ar poilsio" in x for x in exp.unsupported_reasons)


def _save(wb, tmp_path):
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return p


def test_annual_overtime_not_performed_is_explicit_once():
    from core.payroll_rules import annual_overtime_not_performed_finding
    assert check_overtime_limits("D1", (D(5),)) == []                      # istorijos nėra: tyliai nebeprilyginama 0
    note = annual_overtime_not_performed_finding()
    assert note.severity == "info" and "NEPATIKRINTA" in note.detail and note.ref == ""
    assert len(check_overtime_limits("D1", (D(100), D(100)))) == 3          # 200 val. per mėnesį: aišku ir be istorijos


def test_unsupported_rule_year_flagged():
    from core.payroll_rules import SUPPORTED_RULE_YEARS
    assert SUPPORTED_RULE_YEARS == frozenset({2026})
    exp = compute_expected(TERMS, ts(), None, Period(2027, 3))
    assert not exp.supported and any("2027" in x for x in exp.unsupported_reasons)
    assert compute_expected(TERMS, ts(), None, Period(2026, 3)).supported


def test_period_is_required():
    with pytest.raises(TypeError):
        compute_expected(TERMS, ts(), None)
    with pytest.raises(TypeError):
        vdu_daily(hist())


def test_employer_sick_days_first_two_calendar_days_only_if_scheduled():
    """Lapkritis 2026: 1 d. sekmadienis, 2 d. šventė, 3 d. pirmadienis, 4 d. antradienis, 5 d. trečiadienis.

    Pirmos DVI KALENDORINĖS ligos dienos (1 ir 2) neapmokamos, nes tai sekmadienis ir šventė;
    3-5 d. nebeperkeliamos į jų vietą (N5).
    """
    from datetime import date

    from core.payroll_rules import employer_paid_sick_days
    p = Period(2026, 11)
    sick = tuple(date(2026, 11, d) for d in (1, 2, 3, 4, 5))
    row = TimesheetRow("D1", "T", D(0), 0, 5, D(0), (), sick_dates=sick)
    assert employer_paid_sick_days(row, p) == 0
    from_monday = TimesheetRow("D1", "T", D(0), 0, 3, D(0), (),
                               sick_dates=tuple(date(2026, 11, d) for d in (3, 4, 5)))
    assert employer_paid_sick_days(from_monday, p) == 2
    two_periods = TimesheetRow("D1", "T", D(0), 0, 5, D(0), (), sick_dates=tuple(
        date(2026, 11, d) for d in (3, 4, 5, 10, 11, 12)))
    assert employer_paid_sick_days(two_periods, p) == 4        # kiekvieno laikotarpio pirmos 2 d.
    only_weekend_holiday = TimesheetRow("D1", "T", D(0), 0, 2, D(0), (),
                                        sick_dates=(date(2026, 11, 1), date(2026, 11, 2)))
    assert employer_paid_sick_days(only_weekend_holiday, p) == 0
    terms = EmployeeTerms(D("1000.00"), D("160"), npd_applies=False, sick_pay_rate=D("1"))
    exp = compute_expected(terms, from_monday, hist(), p)
    assert exp.sick_pay == money(vdu_daily(hist(), 20).daily * 2)
    assert compute_expected(terms, row, hist(), p).sick_pay == 0
    none_paid = compute_expected(terms, only_weekend_holiday, hist(), p)
    assert none_paid.sick_pay == 0


def test_weekend_inside_sick_period_does_not_split_it_m1():
    from datetime import date
    from types import SimpleNamespace
    from core.payroll_rules import employer_paid_sick_days
    from core.period import Period
    # L 11-04..11-06 (tre-pen), P 11-07/08 (savaitgalis), L 11-09..11-10: vienas laikotarpis -> 2 apmokamos d.
    ts = SimpleNamespace(sick_days=5, sick_dates=[date(2026, 11, d) for d in (4, 5, 6, 9, 10)])
    assert employer_paid_sick_days(ts, Period(2026, 11)) == 2


def test_sick_dates_across_weekend_are_ambiguous_k3():
    from datetime import date
    from types import SimpleNamespace
    from core.payroll_rules import _sick_gap_ambiguous
    from core.period import Period
    ts = SimpleNamespace(sick_dates=[date(2026, 11, 6), date(2026, 11, 9)])
    assert _sick_gap_ambiguous(ts, Period(2026, 11))
    ts_cont = SimpleNamespace(sick_dates=[date(2026, 11, 3), date(2026, 11, 4)])
    assert not _sick_gap_ambiguous(ts_cont, Period(2026, 11))
