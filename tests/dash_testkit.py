"""Dashboard testų pagalbininkai: laiko užšaldymas ir paruošta DB su HR sesijomis."""
from __future__ import annotations

from datetime import date, datetime

from core.hr import clock

FROZEN_DEFAULT = date(2026, 11, 7)   # šeštadienis: vakar (penktadienis) prekybos diena


def freeze(monkeypatch, day: date = FROZEN_DEFAULT, hour: int = 12) -> None:
    """Pakeičia clock.now, kad dashboard 'šiandien' būtų žinoma."""
    stamp = datetime(day.year, day.month, day.day, hour, 0, tzinfo=clock.TZ).timestamp()
    monkeypatch.setattr(clock, "now", lambda: stamp)
