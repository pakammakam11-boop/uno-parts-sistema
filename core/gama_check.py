"""GAMA išskaitymų (PSD, VSD, pensijų kaupimas, GPM) patikra už mėnesį pagal snapshot'ą.

Taisyklės patikrintos su realiais duomenimis (2026-11-05). Kiekvienas komponentas apvalinamas atskirai
(ROUND_HALF_UP iki cento), tolerancija 0,01 €. Tik skaitymas.
"""
from __future__ import annotations

import re
from decimal import Decimal as D

from core import rates_2026 as r
from core.gama import (ACCRUAL, DEDUCTION, Snapshot, month_lines, month_pay_hours, norm_days, norm_hours,
                       timesheet_hours, variable_ambiguous, variable_value)
from core.models import Finding, money

MODULE = "gama_tax"
PSD_RATE = D("0.0698")
VSD_RATE = D("0.1252")
TOLERANCE = D("0.01")
NOT_IN_BASE = {"201", "911"}                      # ligos 2 d. ir natūros kompensavimas
VACATION_MARKERS = {("300", ACCRUAL), ("701", ACCRUAL), ("302", ACCRUAL)}
SUPPORTED_YEARS = {"2026"}                        # taisyklės (NPD, ribos, tarifai) tik šiems metams
COMPONENTS = ("PSD", "VSD", "PENS", "GPM_LIGA", "GPM", "ALGA", "PRIEDAI_LAIKAS")

# GAMA kintamieji (A02_KINR). Patvirtinti su buhaltere: 10001 pavadinimas GAMA yra "Taikomas NPD", bet realiuose
# duomenyse reikšmė apversta (0 = pilnas NPD taikomas, 1 = NPD netaikomas; 28/28 atvejų, 2026-09).
VAR_NPD_FLAG, VAR_NPD_DISABLED, VAR_PENSION, VAR_SALARY = "10001", "10003", "19203", "100"
NPD_FLAG_FULL_VALUE = D(0)
# Priedų minimumai pagal Darbo kodekso 144 str. (e-TAR, galioja 2026-11): "ne mažesnis kaip".
# Viršvalandžiai >= 1,5x valandos įkainio; naktis >= 1,5x (priedas >= 0,5x, jei FD jau apmokėtas);
# poilsio / švenčių diena >= 2x (priedas >= 1x); viršvalandžiai poilsio dieną / naktį >= 2x, švenčių >= 2,5x
# (persidengimai čia neskiriami, tikrinamas žemiausias galimas minimumas). Mokama daugiau: radinio nėra.
OVERTIME_MULT = D("1.5")
NIGHT_MULTS = (D("0.5"), D("1.5"))
REST_DAY_MULTS = (D("1"), D("2"))
LT_FD, LT_NIGHT, LT_OVERTIME, LT_REST = "1", "101", "102", "107"
WORKED_MARKS = (LT_FD, LT_OVERTIME, LT_NIGHT, LT_REST)   # darbo laiką žymintys tabelio kodai
VAR_FIXED_SUPPLEMENTS = ("801", "805", "806")      # pastovios mėnesio priemokos (A02 TIPAS 4)
VAR_HOURLY_RATE = "101"
AMBIGUOUS_TITLE = "Kintamasis skiriasi pagal padalinį: tikrinti rankiniu būdu"
VACATION_NOTE = "atostoginiai / atleidimas: galimas kito mėnesio priskyrimas"


def npd_full(base: D) -> D:
    """2026 m. NPD: 747 iki MMA, toliau 747 - 0,49 x (DU - MMA), ne mažiau 0."""
    if base <= r.MMA:
        return r.NPD_MAX
    return money(max(D(0), r.NPD_MAX - r.NPD_COEF * (base - r.MMA)))


def _base(lines: dict) -> D:
    return sum((v for (code, tipas), v in lines.items()
                if tipas == ACCRUAL and code not in NOT_IN_BASE), D(0))


def _amount(lines: dict, code: str, tipas: str = DEDUCTION) -> D:
    return money(lines.get((code, tipas), D(0)))


def _expected_gpm(lines: dict, mode: str) -> tuple[D, D]:
    """(GPM, GPM_LIGA) pagal NPD režimą: 'F' = pilnas NPD, '0' = be NPD.

    NPD skaičiuojamas nuo bendros sumos (bazė + ligos išmoka) ir paskirstomas proporcingai;
    pilnas tikslumas iki galutinio apvalinimo kiekvienam komponentui atskirai.
    """
    base = _base(lines)
    sick = lines.get(("201", ACCRUAL), D(0))
    total = base + sick
    npd = npd_full(total) if mode == "F" and total > 0 else D(0)
    npd_base = npd * base / total if total > 0 else D(0)
    npd_sick = npd * sick / total if total > 0 else D(0)
    return (money(max(D(0), base - npd_base) * r.GPM_RATE),
            money(max(D(0), sick - npd_sick) * r.SICK_PAY_GPM_RATE))


def _mode_gap(lines: dict) -> D:
    """Didžiausias komponento skirtumas tarp F ir 0 režimų."""
    f, z = _expected_gpm(lines, "F"), _expected_gpm(lines, "0")
    return max(abs(f[0] - z[0]), abs(f[1] - z[1]))


def _npd_mode(tb: str, by_month: dict, month: str) -> tuple[str, list[str]]:
    """'F' | '0' | 'unknown' pagal ankstesnius informatyvius mėnesius.

    Mėnuo informatyvus, tik jei abiejų režimų tikėtini GPM / GPM_LIGA skiriasi daugiau nei 2 x tolerancija
    ir faktiniai išskaitymai sutampa su VIENU režimu. Dviprasmiai mėnesiai praleidžiami; kai nė vienas
    režimas netinka, etiketė '?'. Nepalaikomų metų mėnesiai žymimi '-' ir nenaudojami.
    """
    pattern, labels = [], []
    for m in sorted(by_month):
        if m >= month or tb not in by_month[m]:
            continue
        if m[:4] not in SUPPORTED_YEARS:
            pattern.append(f"{m}:-")
            continue
        lines = by_month[m][tb]
        if _mode_gap(lines) <= 2 * TOLERANCE:
            continue
        actual = (_amount(lines, "19101"), _amount(lines, "19102"))
        hits = [k for k in ("F", "0")
                if all(abs(a - e) <= TOLERANCE for a, e in zip(actual, _expected_gpm(lines, k)))]
        if len(hits) > 1:
            continue
        label = hits[0] if hits else "?"
        pattern.append(f"{m}:{label}")
        labels.append(label)
    modes = set(labels)
    return (modes.pop() if len(modes) == 1 and modes <= {"F", "0"} else "unknown"), pattern


class _Run:
    def __init__(self, month: str):
        self.month = month
        self.findings: list[Finding] = []
        self.components = {c: {"ok": 0, "mismatch": 0} for c in COMPONENTS}

    def compare(self, tb, comp, expected: D, actual: D, severity="error", note="", title=None):
        diff = actual - expected
        if abs(diff) <= TOLERANCE:
            self.components[comp]["ok"] += 1
            return
        self.components[comp]["mismatch"] += 1
        detail = f"{self.month}, darbuotojas {tb}: tikėtasi {expected}, GAMA {actual}, skirtumas {diff}."
        self.findings.append(Finding(
            module=MODULE, severity=severity, title=title or f"{comp}: neatitikimas {diff:+}",
            detail=detail + (f" ({note})" if note else ""), ref=tb,
            suggested_action="Patikrinti GAMA priskaitymus ir išskaitymus šiam darbuotojui.",
            extra={"component": comp, "month": self.month, "expected": str(expected),
                   "actual": str(actual), "diff": str(diff)}))

    def note(self, tb, severity, title, detail, **extra):
        self.findings.append(Finding(module=MODULE, severity=severity, title=title, detail=detail,
                                     ref=tb, extra={"month": self.month, **extra}))


def annual_history_complete(snapshot: Snapshot, month: str) -> bool:
    """Ar snapshot'o istorija prasideda ne vėliau kaip tų metų sausio 1 d. (metinei 36 VDU ribai)."""
    return bool(snapshot.history_from) and snapshot.history_from[:10] <= f"{month[:4]}-01-01"


def check_month(snapshot: Snapshot, month: str) -> tuple[list[Finding], dict]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError(f"Netaisyklingas mėnuo '{month}', tikimasi YYYY-MM")
    if month[:4] not in SUPPORTED_YEARS:
        raise ValueError(f"Metai {month[:4]} nepalaikomi (palaikomi: {', '.join(sorted(SUPPORTED_YEARS))})")
    if month not in snapshot.months():
        raise ValueError(f"Mėnesio {month} snapshot'e nėra (yra: {', '.join(snapshot.months()) or 'nieko'})")
    by_month = {m: month_lines(snapshot, m) for m in snapshot.months() if m <= month}
    target = by_month.get(month, {})
    run = _Run(month)
    modes = {"F": 0, "0": 0, "unknown": 0}
    annual_done = annual_history_complete(snapshot, month)
    npd_source = {"A02": 0, "istorija": 0, "nežinoma": 0}
    pay_hours = month_pay_hours(snapshot, month)
    has_data = bool(snapshot.variables or snapshot.timesheet)

    for tb in sorted(target):
        lines = target[tb]
        base = _base(lines)
        mode, pattern = _npd_mode(tb, by_month, month)
        mode, source, limited = _apply_npd_flag(run, snapshot, tb, month, mode, pattern)
        npd_source[source] += 1
        modes[mode] += 1

        run.compare(tb, "PSD", money(base * PSD_RATE), _amount(lines, "19201"))
        if _cumulative(by_month, month, tb) > r.SODRA_VSD_CEILING_YEARLY:   # matoma bazė jau virš lubų
            run.note(tb, "warning", "VSD lubos 60 VDU pasiektos: tikrinti rankiniu būdu",
                     f"Sukaupta bazė nuo metų pradžios viršija {r.SODRA_VSD_CEILING_YEARLY} €; "
                     "VSD ir pensijų kaupimas netikrinti.", component="VSD")
        else:
            run.compare(tb, "VSD", money(base * VSD_RATE), _amount(lines, "19202"))
            _check_pension(run, snapshot, by_month, month, tb, lines, base)
        if not limited:
            _check_gpm(run, snapshot, by_month, tb, lines, mode, pattern)
        if has_data:
            _check_salary(run, snapshot, month, tb, lines, pay_hours.get(tb, {}))

    missing = _worked_without_pay_lines(run, snapshot, month, target) if has_data else []

    if not annual_done:
        msg = f"Metinė 36 VDU riba netikrinta: istorija nuo {snapshot.history_from or 'nežinoma'}"
        run.note("*", "warning", msg, msg, component="GPM")

    counts = {s: sum(1 for f in run.findings if f.severity == s) for s in ("error", "warning", "info")}
    summary = {"month": month, "employees": len(target) + len(missing), "components": run.components,
               "npd_modes": modes, "npd_source": npd_source, "findings": counts,
               "annual_check": "atlikta" if annual_done else "neatlikta"}
    return run.findings, summary


def _worked_without_pay_lines(run, snapshot, month, target) -> list[str]:
    """Tabelyje dirbta (bet kuris darbo žymėjimas FD / VD / DN / DP > 0), bet GAMA priskaitymų (A20) nėra visai."""
    missing = []
    for tb in sorted({r.tb for r in snapshot.timesheet if r.month == month} - set(target)):
        ts = timesheet_hours(snapshot, tb, month)
        fd, vd = ts.get(LT_FD, D(0)), ts.get(LT_OVERTIME, D(0))
        if any(ts.get(lt, D(0)) > 0 for lt in WORKED_MARKS):
            run.components["ALGA"]["mismatch"] += 1
            run.note(tb, "error", "Tabelyje dirbta, bet GAMA priskaitymų nėra",
                     f"{month}, darbuotojas {tb}: tabelyje FD {fd} h, VD {vd} h, "
                     f"DN {ts.get(LT_NIGHT, D(0))} h, DP {ts.get(LT_REST, D(0))} h, "
                     "o GAMA šį mėnesį priskaitymų (A20) neturi.", component="ALGA")
            missing.append(tb)
    return missing


def _check_pension(run, snapshot, by_month, month, tb, lines, base):
    """Kaupimas tikrinamas, kai eilutė yra šį mėnesį; dingus po praėjusio mėnesio, tik įspėjimas.

    2026 m. dalis darbuotojų pasitraukė iš kaupimo, todėl senesnė istorija kaupimo nereiškia.
    Kintamasis 19203 (jei yra) lemia: 3 = eilutė privaloma, 0 = eilutės neturi būti.
    """
    flag = variable_value(snapshot, tb, VAR_PENSION, "3", month)
    if flag is None and variable_ambiguous(snapshot, tb, VAR_PENSION, "3", month):
        run.note(tb, "warning", AMBIGUOUS_TITLE,
                 "A02 kintamasis 19203 skiriasi pagal padalinį: pensijų kaupimas netikrintas.", component="PENS")
        return
    if flag is not None:
        run.compare(tb, "PENS", money(base * flag / 100), _amount(lines, "19203"))
        return
    if ("19203", DEDUCTION) in lines:
        run.compare(tb, "PENS", money(base * r.EMPLOYEE_PENSION_ACCUMULATION), _amount(lines, "19203"))
        return
    previous = [m for m in by_month if m < month]
    if base > 0 and previous and ("19203", DEDUCTION) in by_month[max(previous)].get(tb, {}):
        run.note(tb, "warning", "Pensijų kaupimas nutrūko šį mėnesį",
                 f"{max(previous)} buvo 3 % kaupimas, {month} nebėra. Patikrinti, ar darbuotojas pasitraukė.",
                 component="PENS")


def _cumulative(by_month, month, tb) -> D:
    """Bazė nuo metų pradžios iki (imtinai) tikrinamo mėnesio, tik tų pačių metų."""
    year = month[:4]
    return sum((_base(by_month[m][tb]) for m in by_month
                if m[:4] == year and m <= month and tb in by_month[m]), D(0))


PROGRESSIVE_GPM_CODES = ("19104", "19105")   # 25 % / 32 % GPM nuo metinių pajamų dalies


def _check_gpm(run, snapshot, by_month, tb, lines, mode, pattern):
    year = run.month[:4]
    cumulative = _cumulative(by_month, run.month, tb)
    over_36 = cumulative > r.GPM_RATE_25_FROM_YEARLY
    progressive = [c for c in PROGRESSIVE_GPM_CODES if _amount(lines, c)]
    if over_36 or progressive:
        partial = not snapshot.history_from or snapshot.history_from[:10] > f"{year}-01-01"
        detail = f"Sukaupta bazė nuo metų pradžios {money(cumulative)} €, riba {r.GPM_RATE_25_FROM_YEARLY} €."
        if progressive:
            detail += f" GAMA turi progresinio GPM eilučių: {', '.join(progressive)}."
        if partial:
            detail += f" Metinė suma dalinė: istorija prasideda {snapshot.history_from or 'nežinoma'}."
        run.note(tb, "warning", "Metinė riba 36 VDU / progresinis GPM: tikrinti rankiniu būdu", detail,
                 component="GPM", cumulative=str(money(cumulative)))
    else:
        cands = _candidates(lines, mode, 0)
        if cands is None:
            run.note(tb, "info", "NPD režimas nenustatytas",
                     "GPM netikrintas. Istorija: " + (", ".join(pattern) or "nėra informatyvių mėnesių"),
                     component="GPM")
        else:
            special = any(k in lines for k in VACATION_MARKERS)
            _compare_any(run, tb, "GPM", cands, _amount(lines, "19101"),
                         severity="warning" if special else "error", note=VACATION_NOTE if special else "")
    # Ligos išmokai progresiniai tarifai netaikomi (15 %): tikrinama atskirai visais atvejais.
    sick = lines.get(("201", ACCRUAL), D(0))
    if sick or ("19102", DEDUCTION) in lines:
        cands = _candidates(lines, mode, 1)
        if cands is None:
            run.note(tb, "info", "Ligos GPM netikrintas: NPD režimas nenustatytas",
                     "Istorija: " + (", ".join(pattern) or "nėra informatyvių mėnesių"), component="GPM_LIGA")
        else:
            _compare_any(run, tb, "GPM_LIGA", cands, _amount(lines, "19102"))


def _candidates(lines, mode, idx: int) -> list[D] | None:
    """Galimi tikėtini komponento rezultatai: nustatyto režimo arba, kai režimas nežinomas, abiejų,
    jei jie skiriasi ne daugiau nei 2 x tolerancija (kitaip komponentas netikrinamas)."""
    if mode in ("F", "0"):
        return [_expected_gpm(lines, mode)[idx]]
    f, z = _expected_gpm(lines, "F")[idx], _expected_gpm(lines, "0")[idx]
    return [f, z] if abs(f - z) <= 2 * TOLERANCE else None


def _compare_any(run, tb, comp, candidates, actual, severity="error", note=""):
    if any(abs(actual - c) <= TOLERANCE for c in candidates):
        run.components[comp]["ok"] += 1
        return True
    run.compare(tb, comp, candidates[0], actual, severity=severity, note=note)
    return False


def _apply_npd_flag(run, snapshot, tb, month, hist_mode, pattern):
    """(režimas, šaltinis, riboto darbingumo NPD) pagal A02 10001 / 10003, su istorija kaip atsarga."""
    npd_disabled = variable_value(snapshot, tb, VAR_NPD_DISABLED, "3", month)
    if npd_disabled is None and variable_ambiguous(snapshot, tb, VAR_NPD_DISABLED, "3", month):
        run.note(tb, "warning", AMBIGUOUS_TITLE,
                 "A02 kintamasis 10003 skiriasi pagal padalinį: GPM netikrintas.", component="GPM")
        limited = True
    else:
        limited = (npd_disabled or D(0)) > 0
    if limited and npd_disabled is not None:
        run.note(tb, "warning", "Riboto darbingumo NPD: tikrinti rankiniu būdu",
                 "GAMA kintamasis 10003 > 0: GPM netikrintas.", component="GPM")
    flag = variable_value(snapshot, tb, VAR_NPD_FLAG, "3", month)
    if flag is None:
        if variable_ambiguous(snapshot, tb, VAR_NPD_FLAG, "3", month):
            run.note(tb, "warning", AMBIGUOUS_TITLE,
                     "A02 kintamasis 10001 skiriasi pagal padalinį: GPM tikrinamas tik kai NPD režimas rezultato "
                     "nekeičia, kitaip rankiniu būdu.", component="GPM")
            return "unknown", "nežinoma", limited
        return hist_mode, ("istorija" if hist_mode in ("F", "0") else "nežinoma"), limited
    mode = "F" if flag == NPD_FLAG_FULL_VALUE else "0"
    if hist_mode in ("F", "0") and hist_mode != mode:
        run.note(tb, "warning", "NPD nustatymas GAMA nesutampa su faktiniu skaičiavimu",
                 f"Kintamasis 10001 = {flag} ({mode}), istorija rodo {hist_mode}: " + ", ".join(pattern),
                 component="GPM")
    return mode, "A02", limited


def _fixed_supplements(run, snapshot, tb, month) -> tuple[D, bool]:
    """(pastovių priedų 801/805/806 suma, ar žinoma). Dviprasmiškas kintamasis: suma nenaudojama."""
    total, known = D(0), True
    for code in VAR_FIXED_SUPPLEMENTS:
        value = variable_value(snapshot, tb, code, "4", month)
        if value is None and variable_ambiguous(snapshot, tb, code, "4", month):
            run.note(tb, "info", AMBIGUOUS_TITLE,
                     f"A02 kintamasis {code} skiriasi pagal padalinį: pilnas įkainis neskaičiuotas.",
                     component="PRIEDAI_LAIKAS")
            known = False
        total += value or D(0)
    return total, known


def _check_salary(run, snapshot, month, tb, lines, hours):
    ts = timesheet_hours(snapshot, tb, month)
    fd = ts.get(LT_FD)                                   # None = FD eilutės tabelyje nėra (ne 0)
    has_100 = ("100", ACCRUAL) in lines
    # Valandinis režimas: nenulinė 101 eilutė arba (nėra 100 ir yra 101 eilutė / A02 valandos įkainis),
    # kad nulinė 101 suma neišjungtų priedų patikros (Sol S18).
    hourly = _amount(lines, "101", ACCRUAL) != 0 or (
        ("100", ACCRUAL) not in lines and (("101", ACCRUAL) in lines
                                           or variable_value(snapshot, tb, VAR_HOURLY_RATE, "4", month) is not None))
    if has_100 and hourly:
        run.note(tb, "warning", "Mišrus atlygis (100 ir 101): tikrinti rankiniu būdu",
                 "Šį mėnesį yra nenuliniai priskaitymai 100 ir 101.", component="ALGA")
    hours_ok = False
    if fd is not None and ("100", ACCRUAL) in hours:
        actual = hours[("100", ACCRUAL)]                 # tikrinama prieš bet kokį ankstyvą grįžimą
        if abs(actual - fd) > TOLERANCE:
            run.components["ALGA"]["mismatch"] += 1
            run.note(tb, "error", "Valandos nesutampa su tabeliu",
                     f"{month}, darbuotojas {tb}: tabelyje FD {fd} h, GAMA priskaitymo laikas {actual} h.",
                     component="ALGA", expected=str(fd), actual=str(actual))
        else:
            run.components["ALGA"]["ok"] += 1
            hours_ok = True
    elif has_100:
        run.note(tb, "warning", "Valandų patikra neatlikta: tikrinti rankiniu būdu",
                 "Trūksta tabelio FD eilutės arba priskaitymo 100 laiko (LAIKAS).", component="ALGA")
    norm = norm_hours(snapshot, tb, month)
    if hourly and not has_100:
        _check_hourly(run, snapshot, month, tb, lines, ts, fd, norm, hours)
        return
    salary = variable_value(snapshot, tb, VAR_SALARY, "4", month)
    if salary is None and variable_ambiguous(snapshot, tb, VAR_SALARY, "4", month):
        run.note(tb, "info", AMBIGUOUS_TITLE, "A02 kintamasis 100 skiriasi pagal padalinį: alga netikrinta.",
                 component="ALGA")
        return
    # GAMA etatinę algą skaičiuoja pagal dirbtas dienas / normos dienas; valandų variantas paliktas kaip antras.
    candidates = []
    days, ndays = ts.get("606"), norm_days(snapshot, tb, month)
    if salary is not None and days is not None and ndays:
        candidates.append(money(salary * days / ndays))          # dienų formulei valandų normos nereikia
    if salary is not None and fd is not None and norm:
        candidates.append(money(salary * fd / norm))             # valandų formulei reikia FD eilutės ir normos
    salary_ok = False
    if candidates:
        salary_ok = _compare_any(run, tb, "ALGA", candidates, _amount(lines, "100", ACCRUAL))
    else:
        run.note(tb, "info", "Alga netikrinta",
                 "Trūksta atlyginimo kintamojo, tabelio arba mėnesio normos.", component="ALGA")
        if salary is None or not norm:
            return
    if not norm:
        if _extras_present(lines, ts):
            run.note(tb, "info", "Priedai netikrinti: nėra valandų normos", "Nenustatyta mėnesio valandų norma.",
                     component="PRIEDAI_LAIKAS")
        return
    fixed, known = _fixed_supplements(run, snapshot, tb, month)
    rate_full = (salary + fixed) / norm if known else salary / norm
    _check_extras(run, tb, lines, salary / norm, rate_full, ts, salary_ok and hours_ok, fd)


def _check_hourly(run, snapshot, month, tb, lines, ts, fd, norm, hours):
    run.note(tb, "info", "Alga netikrinta: valandinis atlyginimas", "Yra priskaitymas 101.", component="ALGA")
    rate = variable_value(snapshot, tb, VAR_HOURLY_RATE, "4", month)
    if rate is None and variable_ambiguous(snapshot, tb, VAR_HOURLY_RATE, "4", month):
        run.note(tb, "info", AMBIGUOUS_TITLE, "A02 kintamasis 101 skiriasi pagal padalinį: priedai netikrinti.",
                 component="PRIEDAI_LAIKAS")
    elif rate is not None and rate > 0:
        base_ok = (fd is not None and ("101", ACCRUAL) in hours and abs(hours[("101", ACCRUAL)] - fd) <= TOLERANCE
                   and abs(_amount(lines, "101", ACCRUAL) - money(rate * fd)) <= TOLERANCE)
        fixed, known = _fixed_supplements(run, snapshot, tb, month)
        rate_full = rate
        if known and fixed > 0:
            if norm:
                rate_full = rate + fixed / norm
            else:
                run.note(tb, "warning", "Pastovių priedų įtraukti į įkainį nepavyko: nėra normos",
                         "Nenustatyta mėnesio valandų norma.", component="PRIEDAI_LAIKAS")
        _check_extras(run, tb, lines, rate, rate_full, ts, base_ok, fd)
    elif _extras_present(lines, ts):
        run.note(tb, "info", "Priedai netikrinti: nėra valandos įkainio",
                 "A02 kintamasis 101 (TIPAS 4) nerastas.", component="PRIEDAI_LAIKAS")


EXTRA_RULES = (("103", LT_OVERTIME), ("105", LT_NIGHT), ("107", LT_REST))


def _extras_present(lines, ts) -> bool:
    return any(ts.get(lt, D(0)) > 0 or (code, ACCRUAL) in lines for code, lt in EXTRA_RULES)


def _check_extras(run, tb, lines, rate: D, rate_full: D, ts, base_confirmed: bool, fd):
    """Viršvalandžiai / naktis / poilsio dienos: mokama >= DK 144 str. minimumas.

    Tik priedo variantai (0,5x naktis, 1x poilsio) leidžiami, kai bazinis apmokėjimas patvirtintas
    (ALGA patikra praėjo, LAIKAS = FD) ir tipo valandos <= FD; kitaip reikalingas pilnas daugiklis.
    rate = alga / norma (apatinė riba); rate_full prideda pastovias priemokas (801, 805, 806).
    VDI: DK 144 apmokėjimas skaičiuojamas nuo darbo užmokesčio su pastoviomis dalimis; patikrinti su buhaltere.
    """
    rules = (("103", LT_OVERTIME, (OVERTIME_MULT,)), ("105", LT_NIGHT, NIGHT_MULTS), ("107", LT_REST, REST_DAY_MULTS))
    for code, lt, mults in rules:
        hrs = ts.get(lt, D(0))
        actual = _amount(lines, code, ACCRUAL)
        has_line = (code, ACCRUAL) in lines
        if hrs <= 0 and not has_line:
            continue
        if has_line and hrs <= 0 and (lt not in ts or actual != 0):       # eilutės nėra arba aiškus 0 su suma
            run.note(tb, "warning", "Priedas be tabelio valandų: tikrinti rankiniu būdu",
                     f"Priedas {code}: sumokėta {actual}, tabelyje (LT {lt}) "
                     f"{'eilutės nėra' if lt not in ts else 'valandų 0'}.", component="PRIEDAI_LAIKAS",
                     actual=str(actual))
            continue
        if code == "105" and not (base_confirmed and fd is not None and hrs <= fd):
            mults = (max(mults),)                       # naktis: priedas tik patvirtinus bazinį apmokėjimą
        ok_mults = (max(mults),) if code == "107" else mults   # poilsio diena: ok tik pilnas 2x
        cands = {str(m): money(rate * m * hrs) for m in mults}
        minimum = min(cands.values())
        minimum_full = min(money(rate_full * m * hrs) for m in ok_mults)
        shown = {k: str(v) for k, v in cands.items()}
        if actual + TOLERANCE >= minimum_full:
            run.components["PRIEDAI_LAIKAS"]["ok"] += 1
            if len(ok_mults) > 1:
                hit = [m for m, v in cands.items() if abs(actual - v) <= TOLERANCE]
                run.note(tb, "info", f"Priedas {code}: ne mažiau nei DK 144 str. minimumas",
                         f"Sumokėta {actual}, variantai {shown}.", component="PRIEDAI_LAIKAS",
                         variant=hit[0] if hit else "daugiau", candidates=shown)
            continue
        run.components["PRIEDAI_LAIKAS"]["mismatch"] += 1
        if code == "107" and actual + TOLERANCE >= money(rate_full * min(mults) * hrs):
            run.note(tb, "warning", "Poilsio / švenčių dienos darbas: tikrinti, ar valandos apmokėtos bazinėje algoje",
                     f"Priedas {code}, {hrs} h: sumokėta {actual}, tik priedas (1x) arba pilnas apmokėjimas (2x): "
                     f"{shown}. Be valandų apmokėjimo bazinėje algoje reikia 2x.",
                     component="PRIEDAI_LAIKAS", actual=str(actual), candidates=shown)
            continue
        if actual + TOLERANCE >= minimum:
            run.note(tb, "warning", "Galimai per mažai: į valandinį įkainį neįtraukti pastovūs priedai (DK 144 str.)",
                     f"Priedas {code}, {hrs} h: sumokėta {actual}, minimumas pagal algą {minimum}, "
                     f"su pastoviais priedais {minimum_full}. Patikrinti su buhaltere.",
                     component="PRIEDAI_LAIKAS", actual=str(actual), candidates=shown)
            continue
        run.note(tb, "warning", "Mokama mažiau nei DK 144 str. minimumas",
                 f"Priedas {code}, {hrs} h: sumokėta {actual}, minimumas {minimum} (variantai {shown}).",
                 component="PRIEDAI_LAIKAS", actual=str(actual), candidates=shown)
