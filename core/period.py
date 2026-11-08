"""Ataskaitinis laikotarpis (mėnuo) ir Lietuvos šventės. Nieko nėra įrašyta kode konkrečiam mėnesiui."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS_LT = ["sausis", "vasaris", "kovas", "balandis", "gegužė", "birželis",
             "liepa", "rugpjūtis", "rugsėjis", "spalis", "lapkritis", "gruodis"]


def easter_sunday(year: int) -> date:
    """Velykos (Gregoriaus kalendorius, anoniminis algoritmas)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def lt_holidays(year: int) -> set[date]:
    """Lietuvos valstybinės šventės ir atmintinos dienos, kai nedirbama."""
    easter = easter_sunday(year)
    fixed = [(1, 1), (2, 16), (3, 11), (5, 1), (6, 24), (7, 6), (8, 15),
             (11, 1), (11, 2), (12, 24), (12, 25), (12, 26)]
    days = {date(year, m, d) for m, d in fixed}
    days |= {easter, easter + timedelta(days=1)}
    days |= {_first_sunday(year, 5), _first_sunday(year, 6)}
    return days


@dataclass(frozen=True)
class Period:
    year: int
    month: int

    def __post_init__(self):
        if not 1 <= self.month <= 12:
            raise ValueError(f"Neteisingas mėnuo: {self.month}")
        if not 2000 <= self.year <= 2100:
            raise ValueError(f"Neteisingi metai: {self.year}")

    @classmethod
    def parse(cls, text: str) -> "Period":
        try:
            year, month = text.strip().split("-")
            return cls(int(year), int(month))
        except ValueError as exc:
            raise ValueError(f"Laikotarpis turi būti YYYY-MM, gauta: {text!r}") from exc

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label_lt(self) -> str:
        return f"{self.year} m. {MONTHS_LT[self.month - 1]}"

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def workdays(self, holidays: set[date] | None = None) -> int:
        """Darbo dienų skaičius (5 d. savaitė, be švenčių)."""
        if holidays is None:
            holidays = lt_holidays(self.year)
        count, day = 0, self.start
        while day <= self.end:
            if day.weekday() < 5 and day not in holidays:
                count += 1
            day += timedelta(days=1)
        return count
