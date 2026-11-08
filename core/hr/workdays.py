"""Darbo dienos: be savaitgalių ir Lietuvos valstybinių švenčių (palaikomi 2026 ir 2027 m.)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

MIN_DATE, MAX_DATE = date(2026, 1, 1), date(2027, 12, 31)
MAX_RANGE_DAYS = 366


def easter_sunday(year: int) -> date:
    """Velykos (Grigaliaus kalendorius, anoniminis algoritmas)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def holidays(year: int) -> dict[date, str]:
    easter = easter_sunday(year)
    return {
        date(year, 1, 1): "Naujieji metai",
        date(year, 2, 16): "Lietuvos valstybės atkūrimo diena",
        date(year, 3, 11): "Lietuvos nepriklausomybės atkūrimo diena",
        easter: "Šv. Velykos",
        easter + timedelta(days=1): "Antroji Velykų diena",
        date(year, 5, 1): "Tarptautinė darbo diena",
        date(year, 6, 24): "Joninės",
        date(year, 7, 6): "Valstybės (Lietuvos karaliaus Mindaugo karūnavimo) diena",
        date(year, 8, 15): "Žolinė",
        date(year, 11, 1): "Visų šventųjų diena",
        date(year, 11, 2): "Mirusiųjų atminimo diena",
        date(year, 12, 24): "Kūčios",
        date(year, 12, 25): "Kalėdos",
        date(year, 12, 26): "Antroji Kalėdų diena",
    }


def holiday_name(day: date) -> str | None:
    return holidays(day.year).get(day)


def is_workday(day: date) -> bool:
    return day.weekday() < 5 and holiday_name(day) is None


def days_between(start: date, end: date) -> Iterator[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def iter_workdays(start: date, end: date) -> Iterator[date]:
    return (d for d in days_between(start, end) if is_workday(d))


def work_days(start: date, end: date) -> int:
    return sum(1 for _ in iter_workdays(start, end))


def holidays_between(start: date, end: date) -> list[dict]:
    """Šventės laikotarpyje (įskaitant tas, kurios papuola į savaitgalį)."""
    return [{"date": d.isoformat(), "name": name} for d in days_between(start, end)
            if (name := holiday_name(d)) is not None]
