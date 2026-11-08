"""Laikotarpiai ir filialai. Laikotarpiai pagal šiandien (Europe/Vilnius), faktai tik iki vakar; nauja diena be faktų = tuščias intervalas."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from core.hr import clock, workdays
from core.hr.errors import bad_request

PERIODS = ("day", "week", "month", "quarter")
BRANCHES = ("all", "vln", "kns")
BRANCH_LABELS = {"vln": "Vilnius", "kns": "Kaunas"}
MSG_PERIOD = "Neteisingas laikotarpis (day, week, month, quarter)."
MSG_BRANCH = "Neteisingas filialas (all, vln, kns)."


@dataclass(frozen=True)
class Range:
    start: date
    end: date
    label: str

    def days(self) -> list[date]:
        return list(workdays.days_between(self.start, self.end))

    def as_dict(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), "label": self.label}


def validate(period: str, branch: str) -> None:
    if period not in PERIODS:
        raise bad_request(MSG_PERIOD)
    if branch not in BRANCHES:
        raise bad_request(MSG_BRANCH)


def as_of_day(today: date | None = None) -> date:
    """Paskutinė diena su pilnais duomenimis: vakar."""
    return (today or clock.today()) - timedelta(days=1)


def is_trading_day(day: date) -> bool:
    """Parduotuvės dirba pirmadienį-šeštadienį, išskyrus valstybines šventes."""
    return day.weekday() != 6 and workdays.holiday_name(day) is None


def last_trading_day(on_or_before: date) -> date:
    day = on_or_before
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def resolve(period: str, as_of: date) -> Range:
    """Einamasis laikotarpis pagal šiandien (= as_of + 1 d.); pabaiga visada as_of (vakar). start > end = dar nėra faktų."""
    today = as_of + timedelta(days=1)
    if period == "day":
        end = last_trading_day(as_of)
        return Range(end, end, f"Paskutinė darbo diena {end.isoformat()}")
    if period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = month_start(today)
    else:
        start = quarter_start(today)
    name = {"week": "Savaitė", "month": "Mėnuo", "quarter": "Ketvirtis"}[period]
    return Range(start, as_of, f"{name} nuo {start.isoformat()} iki {as_of.isoformat()}")


def previous(period: str, current: Range) -> Range:
    """Palyginamasis laikotarpis tokio pat ilgio, einantis prieš tai."""
    if period == "day":
        end = last_trading_day(current.start - timedelta(days=1))
        return Range(end, end, f"Ankstesnė darbo diena {end.isoformat()}")
    length = current.end - current.start
    if period == "week":
        start = current.start - timedelta(days=7)
    elif period == "month":
        start = add_months(current.start, -1)
    else:
        start = add_months(current.start, -3)
    end = min(start + length, current.start - timedelta(days=1))
    return Range(start, end, f"Ankstesnis laikotarpis nuo {start.isoformat()} iki {end.isoformat()}")


def pct_change(value: float, prev: float) -> float | None:
    """Pokytis % iš sumų; ženklą lemia kryptis net su neigiama baze (/ abs(prev)); None, jei bazė 0."""
    return round((value - prev) / abs(prev) * 100, 1) if prev else None


def ratio_pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def now_local() -> datetime:
    """Dabar Europe/Vilnius laiku, be laiko juostos (taip saugomi laiškų laikai)."""
    return datetime.fromtimestamp(clock.now(), clock.TZ).replace(tzinfo=None)
