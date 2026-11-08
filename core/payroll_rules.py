"""DU taisyklių sluoksnis: tikimas rezultatas patikrai (Rivilė skaičiuoja, mes tikrinam).

Palaikomi atvejai skaičiuojami; nepalaikomi ŽYMIMI (`supported=False` + priežastys), ne spėjami.
Šaltiniai: .flow/uno-real/RESEARCH.md (✅ / 🟡 / ⚠️ žymės prie kiekvienos konstantos).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal as D

from core import rates_2026 as r
from core.models import Finding, money
from core.payroll import gross_to_net, net_to_gross
from core.period import Period, lt_holidays

# Metai, kuriems turime taisykles ir tarifus (rates_2026). Kitiems metams DU neskaičiuojamas (supported=False).
SUPPORTED_RULE_YEARS = frozenset({2026})

# --- Koeficientai (🟡 Grok; ⚠️ DK 144 str. kombinacijas tikslinti) ---
MULT_OVERTIME = D("1.5")
MULT_NIGHT = D("1.5")
MULT_REST_OR_HOLIDAY = D("2.0")
MULT_OVERTIME_ON_HOLIDAY = D("2.5")
MULT_OVERTIME_AT_NIGHT = D("2.0")
MULTIPLIERS = {
    "overtime": MULT_OVERTIME,
    "night": MULT_NIGHT,
    "rest_day": MULT_REST_OR_HOLIDAY,
    "holiday": MULT_REST_OR_HOLIDAY,
    "overtime_holiday": MULT_OVERTIME_ON_HOLIDAY,
    "overtime_night": MULT_OVERTIME_AT_NIGHT,
}

# --- Viršvalandžių ribos (✅ VDI) ---
OVERTIME_WEEK_LIMIT = D("8")
OVERTIME_WEEK_LIMIT_CONSENT = D("12")
OVERTIME_YEAR_LIMIT = D("180")

# --- Ligos išmoka (✅ SADM): 62,06-100 % VDU, darbdavys moka pirmas 2 d.; 15 % GPM (✅ VMI) ---
SICK_RATE_MIN = D("0.6206")
SICK_RATE_MAX = D("1")
SICK_EMPLOYER_DAYS = 2
SICK_GPM_RATE = r.SICK_PAY_GPM_RATE

# --- VDU (🟡; nut. Nr. 496) ---
VDU_MONTHS = 3
VDU_INCLUDED = frozenset({"salary", "overtime", "night", "holiday", "bonus"})
VDU_EXCLUDED = frozenset({"vacation_pay", "sick_pay", "per_diem", "downtime"})

REASON_NPD_SICK = "NPD ir ligos išmokos sąveika: tikslinti su buhaltere"
REASON_HOLIDAY_WORK = "Darbas švenčių ar poilsio dieną: buhalterės patikra"
REASON_SICK_PERIODS = "Ligos laikotarpiai nevienareikšmiai (tarp jų savaitgalis ar šventė): tikslinti pagal pažymas"



@dataclass(frozen=True)
class EmployeeTerms:
    net_agreed: D
    hours_norm: D
    fixed_term: bool = False
    pension_accumulation: bool = False
    npd_applies: bool = True          # NPD tik pas vieną darbdavį, prašymu
    sick_pay_rate: D = SICK_RATE_MIN
    hired_this_month: bool = False
    employers_count: int = 1

    def __post_init__(self):
        if not SICK_RATE_MIN <= self.sick_pay_rate <= SICK_RATE_MAX:
            raise ValueError(f"sick_pay_rate turi būti {SICK_RATE_MIN}..{SICK_RATE_MAX}")


@dataclass(frozen=True)
class MonthIncome:
    month: str                        # "2026-09"
    worked_days: D
    components: dict[str, D]


@dataclass(frozen=True)
class VduResult:
    daily: D
    from_history: D
    floor: D
    used_floor: bool


@dataclass(frozen=True)
class Expected:
    gross_salary: D
    gross_overtime: D
    npd: D
    gpm: D
    sodra_employee: D
    sodra_employer: D
    net: D
    sick_pay: D
    sick_gpm: D
    supported: bool
    unsupported_reasons: list[str] = field(default_factory=list)

    @property
    def gross_total(self) -> D:
        return self.gross_salary + self.gross_overtime + self.sick_pay


def vdu_daily(history: list[MonthIncome], workdays_current_month: int) -> VduResult:
    """Vidutinis dienos uždarbis: 3 kalendoriniai mėnesiai prieš.

    Taisyklė pagal LRV nutarimą Nr. 496; nutarimas Nr. 814 (2026-11-01) dar NEperžiūrėtas.
    Įskaitoma: alga, priemokos (viršvalandžiai, naktis, šventės), premijos. Neįskaitoma: atostoginiai,
    ligos išmokos, dienpinigiai, prastova. Dienos = išdirbtos dienos tuose mėnesiuose;
    apatinė riba = MMA / einamo mėnesio darbo dienų.
    """
    if len(history) != VDU_MONTHS:
        raise ValueError(f"VDU reikia {VDU_MONTHS} mėnesių istorijos, gauta {len(history)}")
    income, days = D(0), D(0)
    for m in history:
        for key, value in m.components.items():
            if key in VDU_INCLUDED:
                income += D(value)
            elif key not in VDU_EXCLUDED:
                raise ValueError(f"Nežinomas VDU komponentas: {key}")
        days += m.worked_days
    floor = money(r.MMA / D(workdays_current_month))
    from_history = money(income / days) if days > 0 else D("0.00")
    daily = max(from_history, floor)
    return VduResult(daily, from_history, floor, used_floor=from_history < floor)


def overtime_pay(hourly_gross: D, kind: str, hours: D) -> D:
    """Apmokėjimas už valandas pagal koeficientą (visa suma, ne vien priedas)."""
    if kind not in MULTIPLIERS:
        raise ValueError(f"Nežinomas koeficiento tipas: {kind}")
    return money(hourly_gross * MULTIPLIERS[kind] * hours)


def annual_overtime_not_performed_finding() -> Finding:
    """Vienas info įrašas per paleidimą (ne per darbuotoją): metinė 180 val. riba nepatikrinta be metų istorijos."""
    return Finding("payroll", "info", "Metinė viršvalandžių riba nepatikrinta",
                   f"Metinė {OVERTIME_YEAR_LIMIT} val. viršvalandžių riba NEPATIKRINTA: nėra ankstesnių mėnesių "
                   "viršvalandžių istorijos. Tikrinamos tik savaitinės ribos ir šio mėnesio suma.",
                   suggested_action="Apskaitininke, patikrinkite metų viršvalandžių sumą Rivilėje.",
                   extra={"kind": "annual_overtime_not_checked"})


def check_overtime_limits(tab_no: str, by_week: tuple[D, ...] | list[D], *,
                          consent: bool = False, year_to_date: D | None = None) -> list[Finding]:
    """8 val. / 7 d. (12 su darbuotojo sutikimu), 180 val. per metus.

    `year_to_date=None` = metų istorijos nėra: metinė riba netikrinama (kviečiantysis prideda
    `annual_overtime_not_performed_finding()` vieną kartą), bet šio mėnesio suma > 180 val. vis tiek klaida.
    """
    week_limit = OVERTIME_WEEK_LIMIT_CONSENT if consent else OVERTIME_WEEK_LIMIT
    out = []
    for i, hours in enumerate(by_week, 1):
        if hours > week_limit:
            out.append(Finding("payroll", "error", "Viršvalandžių savaitės riba viršyta",
                               f"{tab_no}: {i} sav. viršvalandžių {hours} val., riba {week_limit} val.",
                               ref=tab_no, extra={"week": i, "limit": str(week_limit)}))
    total_year = (year_to_date or D(0)) + sum(by_week, D(0))
    if total_year > OVERTIME_YEAR_LIMIT:
        out.append(Finding("payroll", "error", "Viršvalandžių metinė riba viršyta",
                           f"{tab_no}: per metus {total_year} val., riba {OVERTIME_YEAR_LIMIT} val.", ref=tab_no))
    return out


def _unsupported(terms: EmployeeTerms, ts, history, period: Period) -> list[str]:
    reasons = []
    if period.year not in SUPPORTED_RULE_YEARS:
        reasons.append(f"{period.year} m. DU taisyklių nėra (palaikomi tik {sorted(SUPPORTED_RULE_YEARS)}): "
                       "skaičiavimas nepalaikomas")
    if terms.hired_this_month:
        reasons.append("Priimtas einamą mėnesį: dalinio mėnesio skaičiavimas nepalaikomas")
    if terms.employers_count > 1:
        reasons.append("Keli darbdaviai: NPD ir ribos nepalaikomi")
    if ts.vacation_days:
        reasons.append("Atostoginiai (VDU) dar nepalaikomi")
    if getattr(ts, "night_hours", D(0)):
        reasons.append("Naktinio darbo deriniai nepalaikomi (DK 144 str. tikslinti)")
    if getattr(ts, "holiday_hours", D(0)):
        reasons.append(REASON_HOLIDAY_WORK)
    if ts.sick_days and not history:
        reasons.append("Trūksta 3 mėn. istorijos VDU (ligos išmokai)")
    if ts.sick_days and terms.npd_applies:
        reasons.append(REASON_NPD_SICK)
    if _sick_gap_ambiguous(ts, period):
        reasons.append(REASON_SICK_PERIODS)
    return reasons


def _sick_gap_ambiguous(ts, period: Period) -> bool:
    """K3: ligos datos, tarp kurių tik savaitgalis / šventė, gali būti vienas arba du nedarbingumai.

    Be tikrų pažymų laikotarpių to nespėliojame: atvejis eina buhalterei.
    """
    dates = sorted(set(getattr(ts, "sick_dates", ()) or ()))
    holidays = lt_holidays(period.year)
    return any((b - a).days > 1 and _only_rest_days_between(a, b, holidays) for a, b in zip(dates, dates[1:]))


def _calc(terms: EmployeeTerms, gross: D):
    """gross_to_net su galimybe išjungti NPD."""
    res = gross_to_net(gross, fixed_term=terms.fixed_term, pension_accumulation=terms.pension_accumulation)
    if terms.npd_applies:
        return res
    gpm = money(r.GPM_RATE * gross)
    return type(res)(res.gross, D("0.00"), gpm, res.sodra_employee, gross - res.sodra_employee - gpm,
                     res.sodra_employer, res.employer_cost)


def _search_no_npd(net: D, terms: EmployeeTerms) -> D:
    """Mažiausias bruto (centais), kurio neto >= tikslo, kai NPD netaikomas."""
    lo, hi = 0, int(net * 300) + 100_000
    while lo < hi:
        mid = (lo + hi) // 2
        if _calc(terms, D(mid) / 100).net >= net:
            hi = mid
        else:
            lo = mid + 1
    return D(lo) / 100


def _gross_from_net(terms: EmployeeTerms, net: D) -> D:
    if terms.npd_applies:
        return net_to_gross(net, fixed_term=terms.fixed_term,
                            pension_accumulation=terms.pension_accumulation).gross
    return _search_no_npd(net, terms)


def _sick_periods(dates: list[date], holidays: set[date] | frozenset = frozenset()) -> list[list[date]]:
    """Ligos datos -> ligos laikotarpiai (kalendorinės dienos).

    Tabelyje savaitgalis ar šventė per ligą žymimi P, ne L, todėl tarpas, kurį sudaro tik ne darbo dienos,
    laikomas tuo pačiu laikotarpiu, o tarpo dienos įtraukiamos kaip kalendorinės ligos dienos (M1).
    """
    periods: list[list[date]] = []
    for d in dates:
        if periods and _only_rest_days_between(periods[-1][-1], d, holidays):
            gap = periods[-1][-1] + timedelta(days=1)
            while gap < d:
                periods[-1].append(gap)
                gap += timedelta(days=1)
            periods[-1].append(d)
        else:
            periods.append([d])
    return periods


def _only_rest_days_between(prev: date, nxt: date, holidays) -> bool:
    day = prev + timedelta(days=1)
    while day < nxt:
        if day.weekday() < 5 and day not in holidays:
            return False
        day += timedelta(days=1)
    return True


def employer_paid_sick_days(ts, period: Period) -> int:
    """Darbdavio apmokamos ligos dienos: kiekvieno ligos laikotarpio pirmos 2 KALENDORINĖS dienos,
    iš jų apmokamos tik grafiko darbo dienos (ne savaitgalis, ne šventė).

    Be `sick_dates` (nėra datų) laikoma min(sick_days, 2).
    """
    dates = sorted(set(getattr(ts, "sick_dates", ()) or ()))
    if not dates:
        return min(ts.sick_days, SICK_EMPLOYER_DAYS)
    holidays = lt_holidays(period.year)
    first_days = [d for p in _sick_periods(dates, holidays) for d in p[:SICK_EMPLOYER_DAYS]]
    return sum(1 for d in first_days if d.weekday() < 5 and d not in holidays)


def _sick_pay(terms: EmployeeTerms, ts, history, period: Period) -> tuple[D, D]:
    if not (ts.sick_days and history):
        return D("0.00"), D("0.00")
    vdu = vdu_daily(history, period.workdays())
    pay = money(vdu.daily * terms.sick_pay_rate * employer_paid_sick_days(ts, period))
    return pay, money(pay * SICK_GPM_RATE)


def compute_expected(terms: EmployeeTerms, ts, history: list[MonthIncome] | None, period: Period) -> Expected:
    """Tikėtinas DU: alga (sutarta neto proporcingai) + viršvalandžiai + ligos išmoka (pirmos 2 darbo d.).

    `period` privalomas: nuo jo priklauso darbo dienos, šventės ir ar metams turime taisykles.
    """
    reasons = _unsupported(terms, ts, history, period)
    full_gross = _gross_from_net(terms, money(terms.net_agreed))
    paid_net = money(terms.net_agreed * ts.worked_hours / terms.hours_norm)
    salary = _gross_from_net(terms, paid_net)
    overtime = overtime_pay(full_gross / terms.hours_norm, "overtime", ts.overtime_hours)
    res = _calc(terms, salary + overtime)
    sick, sick_gpm = _sick_pay(terms, ts, history, period)
    net = res.net + sick - sick_gpm
    return Expected(salary, overtime, res.npd, res.gpm + sick_gpm, res.sodra_employee, res.sodra_employer,
                    net, sick, sick_gpm, supported=not reasons, unsupported_reasons=reasons)
