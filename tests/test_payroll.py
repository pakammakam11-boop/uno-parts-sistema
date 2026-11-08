import random
from decimal import Decimal as D

import pytest
from openpyxl import Workbook, load_workbook

from core import world
from core.models import money
from core.payroll import (compare_with_rivile, export_payroll_xlsx, gross_to_net, net_to_gross,
                          run_month)
from core.timesheet import TimesheetRow


def test_gross_1153_by_hand():
    # NPD 747; GPM = 20% * (1153 - 747) = 81.20; Sodra = 19.5% * 1153 = 224.835 -> 224.84
    res = gross_to_net("1153.00")
    assert (res.npd, res.gpm, res.sodra_employee) == (D("747.00"), D("81.20"), D("224.84"))
    assert res.net == D("846.96")
    assert res.sodra_employer == D("20.41")      # 1.77% * 1153 = 20.4081
    assert res.employer_cost == D("1173.41")


def test_gross_1500_npd_decreasing():
    res = gross_to_net(1500)   # NPD = 747 - 0.49*347 = 576.97
    assert res.npd == D("576.97")
    assert res.gpm == D("184.61")   # 0.2 * 923.03 = 184.606
    assert res.sodra_employee == D("292.50")
    assert res.net == D("1022.89")


def test_gross_2500():
    res = gross_to_net(2500)   # NPD = 747 - 0.49*1347 = 86.97
    assert res.npd == D("86.97")
    assert res.gpm == D("482.61")
    assert res.net == D("1529.89")


def test_gross_4000_no_npd():
    res = gross_to_net(4000)
    assert (res.npd, res.gpm, res.sodra_employee, res.net) == (D("0.00"), D("800.00"), D("780.00"), D("2420.00"))


def test_part_time_low_gross_npd_capped_at_gross():
    res = gross_to_net(600)   # NPD 747 > bruto: taikoma tik 600, GPM 0
    assert (res.npd, res.gpm, res.sodra_employee, res.net) == (D("600.00"), D("0.00"), D("117.00"), D("483.00"))


def test_fixed_term_employer_rate():
    assert gross_to_net(1000).sodra_employer == D("17.70")
    assert gross_to_net(1000, fixed_term=True).sodra_employer == D("24.90")


def test_pension_accumulation_extra_3_percent():
    assert gross_to_net(1000, pension_accumulation=True).sodra_employee == D("225.00")


def test_net_to_gross_known_values():
    for gross in ("1153.00", "1500.00", "2500.00", "4000.00"):
        net = gross_to_net(gross).net
        back = net_to_gross(net)       # mažiausias bruto su tuo neto (1153.00 -> 1152.99 duoda tą patį 846.96)
        assert back.net == net and D(gross) - back.gross <= D("0.02")


def test_net_to_gross_roundtrip_200_random_cents():
    rng = random.Random(7)
    for _ in range(200):
        gross = D(rng.randint(10_000, 800_000)) / 100
        net = gross_to_net(gross).net
        back = net_to_gross(net)
        assert back.diff == D("0.00")
        assert gross_to_net(back.gross).net == net    # tikslus iki cento
        assert back.gross <= gross                    # mažiausias toks bruto


def test_net_to_gross_fixed_term_changes_employer_only():
    a, b = net_to_gross(1200), net_to_gross(1200, fixed_term=True)
    assert a.gross == b.gross and b.sodra_employer > a.sodra_employer


def _row(emp, **kw):
    base = dict(tab_no=emp.tab_no, name=emp.full_name, worked_hours=emp.hours_norm, vacation_days=0,
                sick_days=0, overtime_hours=D(0), overtime_by_week=(D(0),) * 5)
    return TimesheetRow(**{**base, **kw})


def test_run_month_full_month_equals_agreed_net():
    rows, summary = run_month(world.EMPLOYEES, [_row(e) for e in world.EMPLOYEES])
    assert all(r.net == r.net_agreed and r.net_diff == 0 for r in rows)
    assert summary.employees == 57


def test_run_month_totals_equal_sum_of_rows():
    ts = [_row(e) for e in world.EMPLOYEES]
    ts[3] = _row(world.EMPLOYEES[3], overtime_hours=D(14))
    emp = world.EMPLOYEES[16]
    ts[16] = _row(emp, worked_hours=emp.hours_norm - 40, vacation_days=5)
    rows, s = run_month(world.EMPLOYEES, ts)
    for attr in ("gross", "npd", "gpm", "sodra_employee", "net", "sodra_employer", "employer_cost"):
        assert getattr(s, attr) == sum((getattr(r, attr) for r in rows), D(0)), attr
    assert s.employer_cost == s.gross + s.sodra_employer


def test_run_month_overtime_vacation_sick_components():
    emp = world.EMPLOYEES[0]
    full = net_to_gross(emp.net_agreed, fixed_term=emp.fixed_term).gross
    ts = _row(emp, worked_hours=emp.hours_norm - 8 * 7, vacation_days=3, sick_days=4, overtime_hours=D(6))
    (row,), _ = run_month([emp], [ts])
    daily = full / 22
    paid_net = money(emp.net_agreed * (ts.worked_hours + 3 * emp.hours_norm / 22) / emp.hours_norm)
    assert row.vacation_pay == net_to_gross(paid_net, fixed_term=emp.fixed_term).gross - row.base_pay  # N1
    assert row.sick_pay == money(daily * D("0.6206") * 2)          # darbdavys max 2 d.
    assert row.overtime_pay == money(full / emp.hours_norm * 6 * D("1.5"))
    assert row.gross == row.base_pay + row.vacation_pay + row.sick_pay + row.overtime_pay
    salary = gross_to_net(row.gross - row.sick_pay, fixed_term=emp.fixed_term)
    # liga: tik 15 % GPM (VMI), Sodros nėra
    assert row.sodra_employee == salary.sodra_employee
    assert row.net == salary.net + row.sick_pay - money(row.sick_pay * D("0.15"))


def test_run_month_missing_timesheet_row_raises():
    with pytest.raises(ValueError):
        run_month(world.EMPLOYEES[:2], [_row(world.EMPLOYEES[0])])


def test_export_and_compare_with_rivile(tmp_path):
    emps = world.EMPLOYEES[:3]
    rows, s = run_month(emps, [_row(e) for e in emps])
    out = export_payroll_xlsx(rows, s, tmp_path / "du.xlsx")
    wb = load_workbook(out)
    ws = wb["Darbo užmokestis"]
    assert [c.value for c in ws[1]] == ["Tab. Nr.", "Vardas Pavardė", "Bruto", "NPD", "GPM",
                                        "Sodra darbuotojo", "Neto", "Sodra darbdavio", "Darbo vietos kaina"]
    assert ws.max_row == 1 + 3 + 1 and ws.cell(5, 1).value == "Iš viso"
    assert round(ws.cell(5, 3).value, 2) == float(s.gross)
    assert "Info" in wb.sheetnames

    riv = Workbook()
    rws = riv.active
    rws.append(["Tab. Nr.", "Bruto", "Neto"])
    for i, r in enumerate(rows):
        rws.append([r.tab_no, float(r.gross) + (5 if i == 1 else 0), float(r.net)])
    riv.save(tmp_path / "riv_du.xlsx")
    findings = compare_with_rivile(rows, tmp_path / "riv_du.xlsx")
    assert [f.ref for f in findings] == [rows[1].tab_no]


def test_half_hours_halve_net_not_gross():
    from dataclasses import replace
    emp = replace(world.EMPLOYEES[0], net_agreed=D("1450.00"), hours_norm=D("176"), fixed_term=False)
    rows, _ = run_month([emp], [_row(emp, worked_hours=D("88"))])
    assert rows[0].net == D("725.00")


def test_run_month_rejects_duplicate_tab_no():
    emp = world.EMPLOYEES[0]
    with pytest.raises(ValueError, match="pasikartoja"):
        run_month([emp], [_row(emp), _row(emp, worked_hours=D(0))])


def test_full_month_with_vacation_keeps_agreed_net_n1():
    from decimal import Decimal as D
    from core.payroll import run_month
    from core.timesheet import TimesheetRow
    from core.world import EMPLOYEES

    emp = next(e for e in EMPLOYEES if e.hours_norm == D(176))
    ts = TimesheetRow(emp.tab_no, emp.full_name, D(136), D(5), D(0), D(0), {})
    rows, _ = run_month([emp], [ts])
    assert rows[0].net == emp.net_agreed
