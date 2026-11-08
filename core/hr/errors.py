"""Verslo klaidos: API jas paverčia į {"detail": "..."} su atitinkamu HTTP kodu."""
from __future__ import annotations


class HrError(Exception):
    def __init__(self, status: int, detail: str, *, commit: bool = False):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.commit = commit  # True: transakcija išsaugoma (pvz. audito įrašas), nors klaida grąžinama


def bad_request(detail: str) -> HrError:
    return HrError(400, detail)


def forbidden(detail: str = "Nepakanka teisių šiam veiksmui") -> HrError:
    return HrError(403, detail)


def not_found(detail: str = "Nerasta") -> HrError:
    return HrError(404, detail)


def conflict(detail: str, *, commit: bool = False) -> HrError:
    return HrError(409, detail, commit=commit)
