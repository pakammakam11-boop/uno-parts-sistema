"""Laikas vienoje vietoje (testuose pakeičiamas): epoch sekundės, data ir ISO laikas Europe/Vilnius."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    TZ = ZoneInfo("Europe/Vilnius")
except ZoneInfoNotFoundError:  # be tzdata (pvz. Windows): fiksuotas +02:00
    TZ = timezone(timedelta(hours=2))


def now() -> float:
    return time.time()


def today() -> date:
    return datetime.fromtimestamp(now(), TZ).date()


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds") if ts is not None else None


def pretty(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M") if ts is not None else ""
