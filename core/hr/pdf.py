"""PDF generavimas (reportlab): prašymas, komandiruotės įsakymas ir pasirašytas variantas su parašų lapu.

Pasirašytas PDF = tie patys originalo puslapiai (perpiešiami iš to paties payload, invariant=1 duoda tuos pačius
baitus) + papildomas lapas „Demo parašas (imitacija)“.
"""
from __future__ import annotations

import io
import textwrap

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from core.invoices import FONT, FONT_BOLD, register_fonts

LEFT, TOP, WRAP = 56, A4[1] - 64, 88
ROLE_LABELS = {"employee": "Darbuotojas", "substitute": "Pavaduojantis", "manager": "Vadovas"}
LEAVE_LABELS = {"annual": "Kasmetinės atostogos", "unpaid": "Nemokamos atostogos", "study": "Mokymosi atostogos"}


class _Page:
    def __init__(self, c: canvas.Canvas):
        self.c, self.y = c, TOP

    def title(self, text: str) -> None:
        self.c.setFont(FONT_BOLD, 15)
        self.c.drawString(LEFT, self.y, text)
        self.y -= 30

    def line(self, text: str, *, bold: bool = False, size: int = 10, gap: int = 15) -> None:
        self.c.setFont(FONT_BOLD if bold else FONT, size)
        self.c.drawString(LEFT, self.y, text)
        self.y -= gap

    def field(self, label: str, value: str) -> None:
        self.c.setFont(FONT, 9)
        self.c.setFillGray(0.4)
        self.c.drawString(LEFT, self.y, label)
        self.c.setFillGray(0)
        self.c.setFont(FONT_BOLD, 11)
        wrapped = textwrap.wrap(value or "-", WRAP - 12) or ["-"]
        for part in wrapped:
            self.y -= 14
            self.c.drawString(LEFT, self.y, part)
        self.y -= 12

    def gap(self, size: int = 10) -> None:
        self.y -= size


def _request_page(page: _Page, p: dict) -> None:
    page.line(p["company"], bold=True, size=11)
    page.line(f"Dokumento Nr. {p['number']}  ·  {p['created']}", size=9, gap=26)
    if p["type"] == "trip":
        page.title("Prašymas dėl komandiruotės")
    else:
        page.title(f"Prašymas suteikti atostogas ({p['leave_kind_label'].lower()})")
    page.field("Darbuotojas", f"{p['employee']['name']}, {p['employee']['position']}, {p['employee']['department']}")
    page.field("Laikotarpis", f"{p['date_from']} iki {p['date_to']} ({p['work_days']} darbo d.)")
    if p["type"] == "trip":
        page.field("Kelionės tikslas (vieta)", p["destination"])
        page.field("Komandiruotės paskirtis", p["purpose"])
    page.field("Pavaduojantis (pasirašydamas sutinka pavaduoti)", p["substitute"])
    page.field("Vadovas", p["manager"])
    if p.get("comment"):
        page.field("Komentaras", p["comment"])
    page.gap()
    page.line("Dokumentas pasirašomas elektroniniu parašu. Pasirašius dokumentas nebekeičiamas.", size=8)


def _order_page(page: _Page, p: dict) -> None:
    page.line(p["company"], bold=True, size=11)
    page.line(f"Įsakymas Nr. {p['number']}  ·  {p['created']}", size=9, gap=26)
    page.title("Įsakymas dėl komandiruotės")
    page.line("Siunčiu į komandiruotę:", size=11, gap=20)
    page.field("Darbuotojas", f"{p['employee']['name']}, {p['employee']['position']}")
    page.field("Vieta", p["destination"])
    page.field("Laikotarpis", f"{p['date_from']} iki {p['date_to']}")
    page.field("Paskirtis", p["purpose"])
    page.field("Pagrindas", f"Darbuotojo prašymas Nr. {p['request_number']}")
    page.field("Įsakymą išleidžia", p["manager"])


def _signature_page(page: _Page, signatures: list[dict], sha256: str) -> None:
    page.title("Demo parašas (imitacija)")
    page.line("Šis lapas imituoja e. parašo liudijimą. Tikras Dokobit parašas demo versijoje nenaudojamas.", size=8, gap=22)
    for s in signatures:
        page.field(f"{s['role_label']} · {s['method']}", f"{s['name']}  ·  {s['signed_at']}")
    page.gap(6)
    page.line("Dokumento sha256 (originalas)", size=8, gap=12)
    page.line(sha256[:32], size=9, gap=12)
    page.line(sha256[32:], size=9)


def render(payload: dict, *, signatures: list[dict] | None = None, sha256: str = "") -> bytes:
    """Originalus PDF (be signatures) arba pasirašytas (originalas + parašų lapas)."""
    register_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(payload["title"])
    (_order_page if payload["kind"] == "order" else _request_page)(_Page(c), payload)
    c.showPage()
    if signatures is not None:
        _signature_page(_Page(c), signatures, sha256)
        c.showPage()
    c.save()
    return buf.getvalue()
