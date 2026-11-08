from decimal import Decimal as D

import pytest
from openpyxl import Workbook, load_workbook

from core import employees as emp


def test_roundtrip_world_employees(tmp_path):
    records = emp.from_world()
    path = emp.write_employees_xlsx(records, tmp_path / "d.xlsx")
    parsed = emp.parse_employees(path)
    assert len(parsed) == 57
    assert [(r.tab_no, r.net_agreed, r.hours_norm, r.fixed_term) for r in parsed] == \
           [(r.tab_no, r.net_agreed, r.hours_norm, r.fixed_term) for r in records]
    assert parsed[0].npd_applies and parsed[0].sick_pay_percent == D("62.06")
    assert parsed[0].terms().sick_pay_rate == D("0.6206")


def test_template_has_headers_and_info_sheet(tmp_path):
    path = emp.write_employees_template(tmp_path / "sablonas.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == ["Darbuotojai", "Info"]
    assert [c.value for c in wb["Darbuotojai"][1]] == emp.COLUMNS
    with pytest.raises(emp.EmployeesError, match="nėra nė vieno darbuotojo"):
        emp.parse_employees(path)


def _file(tmp_path, *rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.append(header or emp.COLUMNS)
    for r in rows:
        ws.append(r)
    path = tmp_path / "x.xlsx"
    wb.save(path)
    return path


def test_defaults_and_lithuanian_numbers(tmp_path):
    path = _file(tmp_path, ["D1", "A B", "R1", "1 250,50", "176,0"])
    r = emp.parse_employees(path)[0]
    assert r.net_agreed == D("1250.50") and r.hours_norm == D("176.0")
    assert (r.fixed_term, r.pension_accumulation, r.npd_applies, r.hired_this_month) == (False, False, True, False)


def test_validation_errors_name_row_and_column(tmp_path):
    path = _file(tmp_path,
                 ["D1", "A B", "R1", 1000, 176, "taip", "ne", "ne", "50", "ne"],
                 ["D1", "", "R1", -5, 0, "x", "", "", "", ""])
    with pytest.raises(emp.EmployeesError) as exc:
        emp.parse_employees(path)
    text = "\n".join(exc.value.errors)
    assert "Eilutė 2, stulpelis „Ligos apmokėjimas %“" in text
    assert "Eilutė 3, stulpelis „Vardas Pavardė“: reikšmė privaloma" in text
    assert "Eilutė 3, stulpelis „Sutarta neto“" in text
    assert "Eilutė 3, stulpelis „Valandų norma“" in text
    assert "Eilutė 3, stulpelis „Terminuota“" in text
    assert "Eilutė 3, stulpelis „Tab. Nr.“: „D1“ jau yra eilutėje 2" in text


def test_missing_required_column(tmp_path):
    path = _file(tmp_path, ["D1", "A"], header=["Tab. Nr.", "Vardas Pavardė"])
    with pytest.raises(emp.EmployeesError, match="Trūksta privalomų stulpelių"):
        emp.parse_employees(path)
