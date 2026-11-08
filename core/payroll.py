"""Darbo užmokestis: bruto <-> neto, mėnesio skaičiavimas, eksportas.

UNO susitaria dėl NETO ("į rankas"), todėl buhalteris kas mėnesį turi gauti bruto iš neto.
Visi pinigai Decimal, kiekvienas komponentas apvalinamas iki cento (ROUND_HALF_UP).

Formulės (2026, parametrai `core/rates_2026.py`):
  Sodra darbuotojo = 19,5 % x bruto (+3 % jei kaupia pensijai)
  NPD = 747, kai bruto <= MMA (1153); kitaip max(0, 747 - 0,49 x (bruto - 1153))
  GPM = 20 % x max(0, bruto - NPD)
  Neto = bruto - Sodra darbuotojo - GPM
  Sodra darbdavio = 1,77 % (2,49 %, jei terminuota sutartis)

Demo supaprastinimai (pasakyti klientui):
  - NPD netaikomas proporcingai pagal etatą, taikoma pilna formulė.
  - Atostoginiai = vidutinis dienos bruto x atostogų dienų sk. (ne tikras VDU per 3 mėn.).
    Vidutinis dienos bruto = pilno mėnesio bruto / 22 d.
  - Ligos 1-2 d. apmoka darbdavys 62,06 % vidutinio dienos bruto; nuo 3 d. moka Sodra (į DU neįeina).
  - Viršvalandžiai = valandinis bruto x 1,5 (pilno mėnesio bruto / valandų norma).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from core import rates_2026 as r
from core.models import Employee, Finding, money
from core.timesheet import TimesheetRow
from core.world import WORKDAYS_OCT_2026

SICK_EMPLOYER_RATE = D("0.6206")   # 62,06 % vidutinio uždarbio
SICK_EMPLOYER_MAX_DAYS = 2
CENT = D("0.01")


@dataclass(frozen=True)
class PayResult:
    gross: D
    npd: D               # faktiškai pritaikytas NPD (ne daugiau už bruto)
    gpm: D
    sodra_employee: D
    net: D
    sodra_employer: D
    employer_cost: D     # bruto + Sodra darbdavio
    diff: D = D("0.00")  # net_to_gross: gautas neto - prašytas neto (0, kai tiksliai)


@dataclass(frozen=True)
class PayslipRow:
    tab_no: str
    name: str
    worked_hours: D
    base_pay: D
    vacation_pay: D
    sick_pay: D
    overtime_pay: D
    gross: D
    npd: D
    gpm: D
    sodra_employee: D
    net: D
    sodra_employer: D
    employer_cost: D
    net_agreed: D
    net_diff: D          # faktinis neto - sutartas neto (mažėja dėl neišdirbtų valandų)


@dataclass(frozen=True)
class Summary:
    employees: int
    gross: D
    npd: D
    gpm: D
    sodra_employee: D
    net: D
    sodra_employer: D
    employer_cost: D


def npd_for(gross: D) -> D:
    """NPD pagal formulę (dar neapribotas bruto suma)."""
    if gross <= r.MMA:
        return r.NPD_MAX
    return max(D(0), money(r.NPD_MAX - r.NPD_COEF * (gross - r.MMA)))


def gross_to_net(gross, *, fixed_term: bool = False, pension_accumulation: bool = False) -> PayResult:
    g = money(gross)
    npd = min(npd_for(g), g)
    gpm = money(r.GPM_RATE * max(D(0), g - npd))
    rate = r.EMPLOYEE_SODRA + (r.EMPLOYEE_PENSION_ACCUMULATION if pension_accumulation else D(0))
    sodra = money(g * rate)
    employer_rate = r.EMPLOYER_SODRA_FIXED_TERM if fixed_term else r.EMPLOYER_SODRA_INDEFINITE
    sodra_er = money(g * employer_rate)
    return PayResult(g, npd, gpm, sodra, g - sodra - gpm, sodra_er, g + sodra_er)


def _net_cents(cents: int, fixed_term: bool, pension: bool) -> D:
    return gross_to_net(D(cents) * CENT, fixed_term=fixed_term, pension_accumulation=pension).net


def net_to_gross(net, *, fixed_term: bool = False, pension_accumulation: bool = False) -> PayResult:
    """Mažiausias bruto (centais), kurio gross_to_net(...).net == net.

    Neto beveik monotoniškas bruto atžvilgiu (dėl atskiro apvalinimo +-1 ct), todėl:
    dvejetainė paieška pirmam bruto su neto >= tikslo, tada patikra +-20 ct lange.
    Jei tikslaus neto nėra, imamas artimiausias ir rodomas `diff`.
    """
    target = money(net)
    lo, hi = 0, int(target * 100 * 3) + 100_000
    while lo < hi:
        mid = (lo + hi) // 2
        if _net_cents(mid, fixed_term, pension_accumulation) >= target:
            hi = mid
        else:
            lo = mid + 1
    window = range(max(0, lo - 20), lo + 21)
    best = min(window, key=lambda c: (abs(_net_cents(c, fixed_term, pension_accumulation) - target), c))
    res = gross_to_net(D(best) * CENT, fixed_term=fixed_term, pension_accumulation=pension_accumulation)
    return PayResult(**{**res.__dict__, "diff": res.net - target})


def _payslip(emp: Employee, ts: TimesheetRow) -> PayslipRow:
    full = net_to_gross(emp.net_agreed, fixed_term=emp.fixed_term).gross   # pilno mėnesio bruto
    hourly = full / emp.hours_norm
    daily = full / D(WORKDAYS_OCT_2026)
    # Sutarta NETO: proporcingai mažinamas neto, o ne bruto (S1)
    base_net = money(emp.net_agreed * ts.worked_hours / emp.hours_norm)
    base = net_to_gross(base_net, fixed_term=emp.fixed_term).gross
    # Atostogos apmokamos kaip įprastas laikas: dirbtas + atostogų laikas konvertuojamas kartu (N1)
    vac_hours = ts.vacation_days * emp.hours_norm / D(WORKDAYS_OCT_2026)
    paid_net = money(emp.net_agreed * min(D(1), (ts.worked_hours + vac_hours) / emp.hours_norm))
    vacation = net_to_gross(paid_net, fixed_term=emp.fixed_term).gross - base if ts.vacation_days else D("0.00")
    sick = money(daily * SICK_EMPLOYER_RATE * min(ts.sick_days, SICK_EMPLOYER_MAX_DAYS))
    overtime = money(hourly * ts.overtime_hours * r.OVERTIME_MULTIPLIER)
    res = gross_to_net(base + vacation + overtime, fixed_term=emp.fixed_term)
    # Darbdavio ligos išmoka (1-2 d.): 15 % GPM, be Sodros įmokų (VMI; PSD siūloma tik nuo 2027-07-01)
    sick_gpm = money(sick * r.SICK_PAY_GPM_RATE)
    net = res.net + sick - sick_gpm
    return PayslipRow(
        emp.tab_no, emp.full_name, ts.worked_hours, base, vacation, sick, overtime,
        res.gross + sick, res.npd, res.gpm + sick_gpm, res.sodra_employee, net, res.sodra_employer,
        res.employer_cost + sick, emp.net_agreed, net - emp.net_agreed,
    )


def summarize(rows: list[PayslipRow]) -> Summary:
    def total(attr: str) -> D:
        return money(sum((getattr(x, attr) for x in rows), D(0)))
    return Summary(len(rows), total("gross"), total("npd"), total("gpm"), total("sodra_employee"),
                   total("net"), total("sodra_employer"), total("employer_cost"))


def run_month(employees: list[Employee], timesheet_rows: list[TimesheetRow]) -> tuple[list[PayslipRow], Summary]:
    by_tab: dict[str, TimesheetRow] = {}
    for t in timesheet_rows:
        if t.tab_no in by_tab:
            raise ValueError(f"Tabelyje pasikartoja tabelio numeris {t.tab_no} ({by_tab[t.tab_no].name}; {t.name})")
        by_tab[t.tab_no] = t
    rows = []
    for emp in employees:
        if emp.tab_no not in by_tab:
            raise ValueError(f"Tabelyje nėra darbuotojo {emp.tab_no} {emp.full_name}")
        rows.append(_payslip(emp, by_tab[emp.tab_no]))
    return rows, summarize(rows)


def compare_with_rivile(rows: list[PayslipRow], rivile_payroll_xlsx: str | Path) -> list[Finding]:
    """Rivilė DU eksportas: stulpeliai `Tab. Nr.`, `Bruto`, `Neto`. Skirtumas > 0,01 -> Finding."""
    ws = load_workbook(rivile_payroll_xlsx, data_only=True).active
    header = [str(c.value).strip() for c in ws[1]]
    ix = {name: header.index(name) for name in ("Tab. Nr.", "Bruto", "Neto")}
    theirs = {str(v[ix["Tab. Nr."]]): (money(v[ix["Bruto"]]), money(v[ix["Neto"]]))
              for v in ws.iter_rows(min_row=2, values_only=True) if v[ix["Tab. Nr."]]}
    findings = []
    for row in rows:
        gross, net = theirs.get(row.tab_no, (None, None))
        if gross is None:
            findings.append(Finding("payroll", "warning", "Darbuotojo nėra Rivilės DU",
                                    f"{row.tab_no} {row.name} Rivilės DU eksporte nerastas.", ref=row.tab_no))
        elif abs(gross - row.gross) > CENT or abs(net - row.net) > CENT:
            findings.append(Finding(
                "payroll", "error", "DU neatitikimas su Rivile",
                f"{row.tab_no} {row.name}: apskaičiuota bruto {row.gross} / neto {row.net}, Rivilėje {gross} / {net}.",
                ref=row.tab_no,
                suggested_action=f"Apskaitininke, patikrinkite {row.tab_no} {row.name} DU Rivilėje: bruto skirtumas "
                                 f"{gross - row.gross}, neto skirtumas {net - row.net}.",
            ))
    return findings


HEADERS = ["Tab. Nr.", "Vardas Pavardė", "Bruto", "NPD", "GPM", "Sodra darbuotojo", "Neto",
           "Sodra darbdavio", "Darbo vietos kaina"]

INFO_LINES = [
    ("Parametrai", "core/rates_2026.py, patikrinta 2026-11-04"),
    ("Sodra darbuotojo", "19,5 % x bruto (sodra.lt: pensijų 8,72 + ligos 1,99 + motinystės 1,81 + sveikatos 6,98)"),
    ("NPD", "747 EUR, kai bruto <= MMA (1153); kitaip max(0; 747 - 0,49 x (bruto - 1153)). Šaltinis: VMI / KPMG 2026-01"),
    ("GPM", "20 % x max(0; bruto - NPD). Šaltinis: vmi.lt/evmi/5725"),
    ("Neto", "bruto - Sodra darbuotojo - GPM"),
    ("Sodra darbdavio", "1,77 % (neterminuota), 2,49 % (terminuota sutartis) nuo bruto"),
    ("Darbo vietos kaina", "bruto + Sodra darbdavio"),
    ("Bruto iš neto", "UNO sutaria neto; bruto randamas atvirkštine paieška iki cento"),
    ("Atostoginiai (demo)", "atostogų dienos apmokamos kaip darbo laikas: (dirbtos + atostogų val.) / norma x sutartas neto, tada bruto iš neto. Gamyboje: pagal 3 mėn. VDU"),
    ("Liga (demo)", "darbdavys apmoka 1-2 d. po 62,06 % vidutinio dienos bruto, apmokestinama tik 15 % GPM (be Sodros, VMI); nuo 3 d. moka Sodra"),
    ("Viršvalandžiai", "valandinis bruto x 1,5"),
    ("Apvalinimas", "kiekvienas komponentas iki 0,01 (ROUND_HALF_UP)"),
]


def _row_values(x: PayslipRow) -> list:
    return [x.tab_no, x.name, x.gross, x.npd, x.gpm, x.sodra_employee, x.net, x.sodra_employer, x.employer_cost]


def export_payroll_xlsx(rows: list[PayslipRow], summary: Summary, path: str | Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Darbo užmokestis"
    ws.append(HEADERS)
    for x in rows:
        ws.append([v for v in _row_values(x)])
    totals = ["Iš viso", f"{summary.employees} darbuotojai", summary.gross, summary.npd, summary.gpm,
              summary.sodra_employee, summary.net, summary.sodra_employer, summary.employer_cost]
    ws.append([v for v in totals])
    for cell in ws[1] + ws[ws.max_row]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEFGHI", (10, 26, 12, 12, 12, 18, 12, 16, 18)):
        ws.column_dimensions[col].width = width
    info = wb.create_sheet("Info")
    info.append(["Rodiklis", "Formulė / šaltinis"])
    for line in INFO_LINES:
        info.append(list(line))
    info.column_dimensions["A"].width = 22
    info.column_dimensions["B"].width = 110
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
