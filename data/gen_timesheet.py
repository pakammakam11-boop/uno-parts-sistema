"""Spalio 2026 tabelio ir susijusių failų generatorius (deterministinis).

Paleisti: py -3.12 -m data.gen_timesheet
Išvestis: data/generated/tabelis/
  tabelis_2026-10.xlsx, rivile_valandos_2026-10.xlsx,
  sodra_nedarbingumai_2026-10.csv, atostogu_prasymai_2026-10.csv
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from core import world
from core.timesheet import WEEK_RANGES

SEED = 2610
OUT_DIR = Path(__file__).resolve().parent / "generated" / "tabelis"
WEEKENDS = {3, 4, 10, 11, 17, 18, 24, 25, 31}
WORKDAYS = [d for d in range(1, 32) if d not in WEEKENDS]
WEEK_MONDAYS = (5, 12, 19, 26)
N_VACATION, N_SICK, N_OVERTIME = 7, 5, 8


@dataclass
class Plan:
    vacation: list[int] = field(default_factory=list)       # mėnesio dienos
    sick: list[int] = field(default_factory=list)
    overtime: list[int] = field(default_factory=lambda: [0] * len(WEEK_RANGES))
    zero: bool = False
    rivile_hours: int | None = None                         # perrašo Rivilės valandas
    has_request: bool = True
    has_certificate: bool = True


def _day(d: int) -> date:
    return date(world.PERIOD_YEAR, world.PERIOD_MONTH, d)


def _apply_anomalies(plans: dict[str, Plan]) -> None:
    for a in world.TIMESHEET_ANOMALIES:
        p = plans[a.tab_no]
        if a.kind == "overtime_over_limit":
            p.overtime[1] = 14
        elif a.kind == "vacation_no_request":
            p.vacation, p.has_request = [12, 13, 14, 15, 16], False
        elif a.kind == "sick_no_certificate":
            p.sick, p.has_certificate = [20, 21, 22], False
        elif a.kind == "zero_hours":
            p.zero = True
        elif a.kind == "rivile_mismatch":
            p.rivile_hours = 168


def build_plans(employees, rng: random.Random) -> dict[str, Plan]:
    plans = {e.tab_no: Plan() for e in employees}
    flagged = {a.tab_no for a in world.TIMESHEET_ANOMALIES}
    pool = [e.tab_no for e in employees if e.tab_no not in flagged]
    picked = rng.sample(pool, N_VACATION + N_SICK)
    for tab in picked[:N_VACATION]:
        start = rng.choice(WEEK_MONDAYS)
        plans[tab].vacation = list(range(start, start + rng.choice([3, 5])))
    for tab in picked[N_VACATION:]:
        start = rng.choice(WEEK_MONDAYS) + rng.choice([0, 1])
        plans[tab].sick = list(range(start, start + rng.choice([1, 2, 2, 3])))
    for tab in rng.sample(pool, N_OVERTIME):
        plans[tab].overtime[rng.choice([1, 2, 3])] = rng.choice([2, 3, 4, 5, 6])
    _apply_anomalies(plans)
    return plans


def _day_cell(d: int, plan: Plan, day_hours: int):
    if d in WEEKENDS:
        return "P"
    if d in plan.vacation:
        return "A"
    if d in plan.sick:
        return "L"
    return 0 if plan.zero else day_hours


def _write_tabelis(employees, plans, path: Path) -> dict[str, int]:
    wb = Workbook()
    ws = wb.active
    ws.title = "Tabelis 2026-10"
    weeks = [f"Viršvalandžiai {i + 1} sav." for i in range(len(WEEK_RANGES))]
    ws.append(["Tab. Nr.", "Vardas Pavardė", "Padalinys", *range(1, 32), *weeks,
               "Viso val.", "Atostogų d.", "Ligos d."])
    worked: dict[str, int] = {}
    for e in employees:
        p, day_hours = plans[e.tab_no], int(e.hours_norm / world.WORKDAYS_OCT_2026)
        cells = [_day_cell(d, p, day_hours) for d in range(1, 32)]
        worked[e.tab_no] = sum(c for c in cells if isinstance(c, int))
        ws.append([e.tab_no, e.full_name, e.department, *cells, *p.overtime,
                   worked[e.tab_no] + sum(p.overtime), len(p.vacation), len(p.sick)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "D2"
    wb.save(path)
    return worked


def _write_rivile(employees, plans, worked, path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Valandos"
    ws.append(["Tab. Nr.", "Dirbta val.", "Atostogų d.", "Ligos d."])
    for e in employees:
        p = plans[e.tab_no]
        hours = p.rivile_hours if p.rivile_hours is not None else worked[e.tab_no]
        ws.append([e.tab_no, hours, len(p.vacation), len(p.sick)])
    wb.save(path)


def _write_csv(path: Path, plans: dict[str, Plan], days_attr: str, flag_attr: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tab_no", "from", "to"])
        for tab, p in plans.items():
            days = getattr(p, days_attr)
            if days and getattr(p, flag_attr):
                w.writerow([tab, _day(min(days)).isoformat(), _day(max(days)).isoformat()])


def main(out_dir: Path | None = None) -> dict[str, Path]:
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    employees = world.EMPLOYEES
    plans = build_plans(employees, random.Random(SEED))
    paths = {
        "tabelis": out / "tabelis_2026-10.xlsx",
        "rivile": out / "rivile_valandos_2026-10.xlsx",
        "sodra": out / "sodra_nedarbingumai_2026-10.csv",
        "requests": out / "atostogu_prasymai_2026-10.csv",
    }
    worked = _write_tabelis(employees, plans, paths["tabelis"])
    _write_rivile(employees, plans, worked, paths["rivile"])
    _write_csv(paths["sodra"], plans, "sick", "has_certificate")
    _write_csv(paths["requests"], plans, "vacation", "has_request")
    return paths


if __name__ == "__main__":
    for name, p in main().items():
        print(f"{name}: {p}")
