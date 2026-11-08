"""Pirkimo sąskaitų PDF skaitymas, patikros ir pardavimo sąskaitų PDF generavimas.

Skaičiuoja kodas, ne AI: tekstas iš PDF (pdfplumber) -> regex -> Decimal aritmetika.
PDF piešimo variklis (Layout, draw_invoice) čia, nes jį naudoja ir pardavimo sąskaitų
šablonas, ir data/gen_invoices.py (tiekėjų maketai).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import threading
from concurrent.futures import ThreadPoolExecutor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from core import pdf_safe, world
from core.models import Finding, Invoice, InvoiceLine, money

TOLERANCE = Decimal("0.02")
FONT, FONT_BOLD = "UnoArial", "UnoArial-Bold"


# ---------------------------------------------------------------- PDF piešimas

@dataclass(frozen=True)
class Party:
    name: str
    code: str = ""
    vat_code: str = ""
    iban: str = ""
    address: str = ""


@dataclass(frozen=True)
class Layout:
    """Vieno tiekėjo sąskaitos maketas: etiketės ir elementų tvarka."""
    title: str | None
    number_label: str
    issue_label: str
    due_label: str
    seller_label: str
    buyer_label: str
    code_label: str
    vat_label: str
    iban_label: str
    cols: tuple[str, str, str, str, str]
    net_label: str
    vat_total_label: str
    gross_label: str
    date_fmt: str = "%Y-%m-%d"
    buyer_first: bool = False
    meta_last: bool = False
    name_inline: bool = True


@dataclass(frozen=True)
class DocData:
    number: str
    issue: date
    due: date
    seller: Party
    buyer: Party
    lines: tuple[InvoiceLine, ...]
    net: Decimal
    vat: Decimal
    gross: Decimal


CLEAN_LAYOUT = Layout(
    title="PVM SĄSKAITA FAKTŪRA", number_label="Sąskaitos Nr.", issue_label="Išrašymo data",
    due_label="Apmokėti iki", seller_label="Pardavėjas", buyer_label="Pirkėjas",
    code_label="Įmonės kodas", vat_label="PVM kodas", iban_label="IBAN",
    cols=("Aprašymas", "Kiekis", "Kaina be PVM", "PVM", "Suma be PVM"),
    net_label="Suma be PVM", vat_total_label="PVM 21 %", gross_label="Suma su PVM",
)


def fmt_money(value: Decimal) -> str:
    """LT formatas: 1 234,56 (tarpas tūkstančiams, kablelis)."""
    return f"{money(value):,.2f}".replace(",", " ").replace(".", ",")


def register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    candidates = [
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont(FONT, str(regular)))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
            return
    raise RuntimeError("Nerastas TTF šriftas su lietuviškais diakritikais (arial.ttf / DejaVuSans.ttf)")


class _Writer:
    LEFT, RIGHT = 40, 555

    def __init__(self, path: Path):
        register_fonts()
        self.c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        self.y = A4[1] - 50

    def line(self, text: str, *, bold: bool = False, size: int = 10, gap: int = 15) -> None:
        self.c.setFont(FONT_BOLD if bold else FONT, size)
        self.c.drawString(self.LEFT, self.y, text)
        self.y -= gap

    def row(self, left: str, cells: list[tuple[int, str]], *, bold: bool = False) -> None:
        self.c.setFont(FONT_BOLD if bold else FONT, 9)
        self.c.drawString(self.LEFT, self.y, left)
        for x_right, text in cells:
            self.c.drawRightString(x_right, self.y, text)
        self.y -= 15

    def totals(self, label: str, value: str, *, bold: bool = False) -> None:
        self.c.setFont(FONT_BOLD if bold else FONT, 10)
        self.c.drawString(340, self.y, f"{label}:")
        self.c.drawRightString(self.RIGHT, self.y, value)
        self.y -= 16

    def rule(self) -> None:
        self.c.line(self.LEFT, self.y + 10, self.RIGHT, self.y + 10)
        self.y -= 2

    def space(self, n: int = 8) -> None:
        self.y -= n


def _draw_meta(w: _Writer, lay: Layout, doc: DocData) -> None:
    number_line = f"{lay.number_label}: {doc.number}"
    if lay.title is None:
        w.line(number_line, bold=True, size=14, gap=20)
    else:
        w.line(number_line, bold=True, size=11)
    w.line(f"{lay.issue_label}: {doc.issue.strftime(lay.date_fmt)}")
    w.line(f"{lay.due_label}: {doc.due.strftime(lay.date_fmt)}")
    w.space()


def _draw_party(w: _Writer, lay: Layout, label: str, p: Party) -> None:
    if lay.name_inline:
        w.line(f"{label}: {p.name}", bold=True)
    else:
        w.line(label, bold=True)
        w.line(p.name)
    if p.code:
        w.line(f"{lay.code_label}: {p.code}")
    if p.vat_code:
        w.line(f"{lay.vat_label}: {p.vat_code}")
    if p.iban:
        w.line(f"{lay.iban_label}: {p.iban}")
    if p.address:
        w.line(f"Adresas: {p.address}")
    w.space()


def _draw_table(w: _Writer, lay: Layout, doc: DocData) -> None:
    xs = (330, 410, 468, 555)
    d, q, p, v, s = lay.cols
    w.row(d, list(zip(xs, (q, p, v, s))), bold=True)
    w.rule()
    for ln in doc.lines:
        cells = [(xs[0], fmt_money(ln.quantity)), (xs[1], fmt_money(ln.unit_price)),
                 (xs[2], f"{int(ln.vat_rate * 100)} %"), (xs[3], fmt_money(ln.net))]
        w.row(ln.description, cells)
    w.space(10)


def draw_invoice(path: Path, lay: Layout, doc: DocData) -> None:
    """Nupiešia vieno puslapio sąskaitą pagal maketą (spausdinamos sumos imamos iš doc)."""
    w = _Writer(Path(path))
    if lay.title:
        w.line(lay.title, bold=True, size=16, gap=24)
    parties = [("seller", lay.seller_label, doc.seller), ("buyer", lay.buyer_label, doc.buyer)]
    if lay.buyer_first:
        parties.reverse()
    if not lay.meta_last:
        _draw_meta(w, lay, doc)
    for _, label, party in parties:
        _draw_party(w, lay, label, party)
    if lay.meta_last:
        _draw_meta(w, lay, doc)
    _draw_table(w, lay, doc)
    w.totals(lay.net_label, f"{fmt_money(doc.net)} €")
    w.totals(lay.vat_total_label, f"{fmt_money(doc.vat)} €")
    w.totals(lay.gross_label, f"{fmt_money(doc.gross)} €", bold=True)
    w.c.save()


def render_sales_invoice_pdf(invoice: Invoice, path: Path) -> None:
    """Pardavimo sąskaita (UNO kaip pardavėjas) iš švaraus šablono."""
    seller = Party(world.COMPANY.name, world.COMPANY.code, world.COMPANY.vat_code,
                   world.COMPANY.iban, world.COMPANY.address)
    doc = DocData(invoice.number, invoice.issue_date, invoice.due_date, seller,
                  Party(invoice.buyer_name), invoice.lines, invoice.net, invoice.vat, invoice.gross)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    draw_invoice(Path(path), CLEAN_LAYOUT, doc)


# ---------------------------------------------------------------- PDF skaitymas

@dataclass
class ParsedInvoice:
    number: str = ""
    issue_date: date | None = None
    due_date: date | None = None
    supplier_name: str = ""
    supplier_company_code: str = ""
    supplier_vat_code: str = ""
    supplier_iban: str = ""
    lines: list[InvoiceLine] = field(default_factory=list)
    net: Decimal | None = None
    vat: Decimal | None = None
    gross: Decimal | None = None
    confidence: float = 0.0
    source_file: str = ""
    supplier_code: str = ""   # Rivilė tiekėjo kodas (užpildoma atpažinus tiekėją)
    text: str = field(default="", repr=False)   # PDF tekstas (pirkėjo patikrai)
    # (aprašymas, atspausdinta suma, apskaičiuota suma) eilutėms, kurių suma nesutampa su kiekis x kaina
    line_mismatches: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)
    # (aprašymas, atspausdinta suma, apskaičiuota suma) eilutėms, kurių suma nesutampa su kiekis x kaina

    @property
    def missing_fields(self) -> list[str]:
        names = {
            "number": "sąskaitos numeris", "issue_date": "išrašymo data", "due_date": "apmokėjimo terminas",
            "supplier_name": "tiekėjo pavadinimas", "supplier_company_code": "tiekėjo įmonės kodas",
            "supplier_vat_code": "tiekėjo PVM kodas", "supplier_iban": "tiekėjo IBAN",
            "lines": "sąskaitos eilutės", "net": "suma be PVM", "vat": "PVM suma", "gross": "suma su PVM",
        }
        money_keys = {"net", "vat", "gross"}  # 0,00 (pvz. 0 % PVM) nėra trūkstama reikšmė (M1)
        return [label for key, label in names.items()
                if (getattr(self, key) is None if key in money_keys else not getattr(self, key))]


_NUM = r"\d+(?: \d{3})*,\d{2}"
_ROW_RE = re.compile(
    rf"^(?P<desc>.+?)\s+(?P<qty>{_NUM})\s+(?P<price>{_NUM})\s+(?P<rate>\d{{1,2}})\s*%\s+(?P<net>{_NUM})\s*€?$")
_SELLER_HDR = r"(?:Pardavėjas|Tiekėjas|Siuntėjas)"
_BUYER_HDR = r"(?:Pirkėjas|Gavėjas|Užsakovas)"
_TOTALS = {
    "net": r"(?:Suma be PVM|Iš viso be PVM|Bendra suma be PVM)",
    "vat": r"(?:PVM\s*\(?\s*\d+\s*%\s*\)?|PVM suma)",
    "gross": r"(?:Suma su PVM|Iš viso mokėti|Bendra suma mokėti)",
}


def _to_decimal(text: str) -> Decimal:
    return Decimal(text.replace(" ", "").replace(",", "."))


def _to_date(text: str) -> date | None:
    for pattern, fmt in ((r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"), (r"(\d{4}\.\d{2}\.\d{2})", "%Y.%m.%d"),
                         (r"(\d{2}\.\d{2}\.\d{4})", "%d.%m.%Y")):
        m = re.search(pattern, text)
        if m:
            return datetime.strptime(m.group(1), fmt).date()
    return None


def _value(lines: list[str], label_re: str, value_re: str = r".+?") -> str:
    pattern = re.compile(rf"^(?:{label_re})\s*:?\s*({value_re})\s*$")
    for ln in lines:
        m = pattern.match(ln)
        if m:
            return m.group(1).strip()
    return ""


def _party_block(lines: list[str], header_re: str, other_re: str) -> tuple[str, list[str]]:
    """Grąžina (pavadinimas, bloko eilutės) šalies blokui, prasidedančiam antraštės eilute."""
    head = re.compile(rf"^{header_re}\b\s*:?\s*(.*)$")
    stop = re.compile(rf"^{other_re}\b")
    for i, ln in enumerate(lines):
        m = head.match(ln)
        if not m:
            continue
        block: list[str] = []
        for nxt in lines[i + 1:i + 7]:
            if stop.match(nxt):
                break
            block.append(nxt)
        name = m.group(1).strip()
        if not name and block:
            name = block.pop(0)
        return name, block
    return "", []


def _parse_lines(lines: list[str]) -> tuple[list[InvoiceLine], list[tuple[str, Decimal, Decimal]]]:
    """Eilutės ir neatitikimai tarp atspausdintos eilutės sumos bei kiekis x kaina."""
    out: list[InvoiceLine] = []
    mismatches: list[tuple[str, Decimal, Decimal]] = []
    for ln in lines:
        m = _ROW_RE.match(ln)
        if not m:
            continue
        line = InvoiceLine(m["desc"].strip(), _to_decimal(m["qty"]), _to_decimal(m["price"]),
                           Decimal(m["rate"]) / 100)
        out.append(line)
        printed = _to_decimal(m["net"])
        if abs(printed - line.net) > TOLERANCE:
            mismatches.append((line.description, printed, line.net))
    return out, mismatches


def _match_supplier(p: ParsedInvoice) -> str:
    from core import suppliers  # kartoteka = demo + naudotojo sukurti

    hit = suppliers.find(p.supplier_company_code, p.supplier_vat_code, p.supplier_name)
    return hit.rivile_code if hit else ""


def _confidence(p: ParsedInvoice) -> float:
    total = 11
    return round((total - len(p.missing_fields)) / total, 2)


AI_PREFER_MISSING = 4  # tiek ir daugiau trūkstamų laukų = maketas nežinomas, pasitikim AI
AI_WORKERS = 6
PARSE_SLOTS = threading.BoundedSemaphore(2)  # vienu metu ne daugiau 2 PDF vaikų procesų (RAM biudžetas, Sol S7)


def parse_invoice_pdf(path: Path, *, use_ai: bool = False, ai_cache: Path | None = None) -> ParsedInvoice:
    with PARSE_SLOTS:   # ne daugiau 2 PDF vaikų vienu metu
        text = pdf_safe.extract_text(path)   # atskiras procesas su laiko / atminties / dydžio ribomis
    lines = [ln.strip() for ln in text.replace("\xa0", " ").splitlines() if ln.strip()]
    p = ParsedInvoice(source_file=str(path), text=text)
    p.number = _value(lines, r"Sąskaitos\s+Nr\.|PVM\s+sąskaita\s+faktūra\s+Serija/Nr\.|Dokumento\s+Nr\.", r"\S+")
    p.issue_date = _to_date(_value(lines, r"Išrašymo data|Data"))
    p.due_date = _to_date(_value(lines, r"Apmokėti iki|Mokėjimo terminas|Apmokėjimo terminas"))
    p.supplier_name, block = _party_block(lines, _SELLER_HDR, _BUYER_HDR)
    p.supplier_company_code = _value(block, r"Įmonės kodas|Įm\. kodas|Kodas", r"\d{7,9}")
    p.supplier_vat_code = _value(block, r"PVM kodas|PVM mokėtojo kodas|PVM Nr\.", r"LT\d+")
    p.supplier_iban = _value(block, r"IBAN|Banko sąsk\.|A/s", r"LT\d{2}(?: ?\d{4}){4}")
    p.lines, p.line_mismatches = _parse_lines(lines)
    for key, label in _TOTALS.items():
        raw = _value(lines, label, _NUM + r"(?:\s*€)?").replace("€", "").strip()
        setattr(p, key, _to_decimal(raw) if raw else None)
    if use_ai and p.missing_fields:
        from core import invoice_ai  # lazy: demo PDF AI nereikia
        p = invoice_ai.enrich(p, text, prefer_ai=len(p.missing_fields) >= AI_PREFER_MISSING, cache_dir=ai_cache)
    p.supplier_code = _match_supplier(p)
    p.confidence = _confidence(p)
    return p


# ---------------------------------------------------------------- patikros

def _check_arithmetic(p: ParsedInvoice) -> list[Finding]:
    out: list[Finding] = []
    if p.lines and p.vat is not None:
        calc = money(sum((ln.vat for ln in p.lines), Decimal(0)))
        diff = p.vat - calc
        if abs(diff) > TOLERANCE:
            out.append(Finding(
                "invoices", "error", "PVM suma neatitinka eilučių",
                f"Sąskaitoje {p.number} nurodytas PVM {fmt_money(p.vat)} €, o pagal eilutes "
                f"turi būti {fmt_money(calc)} € (skirtumas {fmt_money(diff)} €).",
                ref=p.number,
                suggested_action="Paprašyti tiekėjo pataisytos sąskaitos arba kreditinės; "
                                 "PVM į atskaitą neįtraukti, kol sąskaita nepataisyta.",
                extra={"printed_vat": str(p.vat), "calculated_vat": str(calc), "diff": str(diff)}))
    if p.lines and p.net is not None:
        calc_net = money(sum((ln.net for ln in p.lines), Decimal(0)))
        if abs(p.net - calc_net) > TOLERANCE:
            out.append(Finding(
                "invoices", "error", "Suma be PVM neatitinka eilučių",
                f"Sąskaitoje {p.number} suma be PVM {fmt_money(p.net)} €, eilučių suma {fmt_money(calc_net)} €.",
                ref=p.number, suggested_action="Patikrinti eilutes su tiekėju ir paprašyti pataisymo."))
    if None not in (p.net, p.vat, p.gross) and abs(p.net + p.vat - p.gross) > TOLERANCE:
        out.append(Finding(
            "invoices", "error", "Suma su PVM nesutampa",
            f"Sąskaitoje {p.number}: {fmt_money(p.net)} + {fmt_money(p.vat)} != {fmt_money(p.gross)} €.",
            ref=p.number, suggested_action="Paprašyti tiekėjo pataisytos sąskaitos."))
    return out


def _check_line_totals(p: ParsedInvoice) -> list[Finding]:
    return [Finding(
        "invoices", "error", "Eilutės suma neatitinka",
        f"Sąskaitoje {p.number} eilutė „{desc}“: atspausdinta suma {fmt_money(printed)} €, "
        f"o kiekis x kaina = {fmt_money(calc)} €.",
        ref=p.number,
        suggested_action="Paprašyti tiekėjo pataisyti eilutės sumą ir atsiųsti pataisytą sąskaitą.",
        extra={"printed_line": str(printed), "calculated_line": str(calc)})
        for desc, printed, calc in p.line_mismatches]


def _check_supplier(p: ParsedInvoice) -> list[Finding]:
    if p.supplier_code:
        return []
    return [Finding(
        "invoices", "error", "Nežinomas tiekėjas",
        f"Sąskaita {p.number or p.source_file}: tiekėjas „{p.supplier_name}“ (kodas {p.supplier_company_code or '-'}, "
        f"PVM {p.supplier_vat_code or '-'}) nerastas tiekėjų kartotekoje.",
        ref=p.number,
        suggested_action="Patikrinti tiekėjo rekvizitus (galimas sukčiavimas) ir, jei tikras, "
                         "sukurti kortelę Rivilė tiekėjų kartotekoje prieš importą.",
        extra={"create_supplier": {"name": p.supplier_name, "company_code": p.supplier_company_code,
                                   "vat_code": p.supplier_vat_code, "iban": p.supplier_iban}}
        if p.supplier_name else {})]


def _check_missing(p: ParsedInvoice) -> list[Finding]:
    missing = p.missing_fields
    if not missing:
        return []
    return [Finding(
        "invoices", "warning", "Trūksta laukų sąskaitoje",
        f"Iš {Path(p.source_file).name} nepavyko nuskaityti: {', '.join(missing)}.",
        ref=p.number or Path(p.source_file).name,
        suggested_action="Patikrinti PDF rankiniu būdu arba paprašyti tiekėjo aiškesnės sąskaitos.",
        extra={"missing": missing})]


def _check_duplicates(parsed: list[ParsedInvoice]) -> list[Finding]:
    seen: dict[tuple[str, str], ParsedInvoice] = {}
    out: list[Finding] = []
    for p in parsed:
        if not p.number:
            continue
        key = (p.supplier_code or p.supplier_company_code, p.number)
        first = seen.get(key)
        if first is None:
            seen[key] = p
            continue
        out.append(Finding(
            "invoices", "error", "Dublikatas: sąskaita jau gauta",
            f"Sąskaita {p.number} iš {p.supplier_name} jau yra failuose "
            f"{Path(first.source_file).name} ir {Path(p.source_file).name}.",
            ref=p.number,
            suggested_action="Nesuvesti antrą kartą ir neapmokėti; palikti vieną kopiją."))
    return out


COMPANY_CODE = "305953678"                     # UAB Uno parts (JAR)
COMPANY_NAME_RE = re.compile(r"uno\s*-?\s*parts", re.IGNORECASE)
MSG_NOT_COMPANY = "Ne UNO sąskaita: praleista"


def is_for_company(text: str) -> bool:
    """Sąskaita skirta UAB Uno parts: tekste įmonės kodas arba pavadinimas (pašte būna ir kitų įmonių sąskaitų)."""
    compact = re.sub(r"\s", "", text)
    return COMPANY_CODE in compact or bool(COMPANY_NAME_RE.search(text))


def process_folder(folder: Path, *, use_ai: bool = False, ai_cache: Path | None = None,
                   only_company: bool = False) -> tuple[list[ParsedInvoice], list[Finding]]:
    parsed: list[ParsedInvoice] = []
    findings: list[Finding] = []

    def safe_parse(pdf: Path):
        try:
            return parse_invoice_pdf(pdf, use_ai=use_ai, ai_cache=ai_cache)
        except Exception as exc:  # sugadintas PDF neturi nuversti viso aplanko apdorojimo
            return exc

    pdfs = sorted(Path(folder).glob("*.pdf"))
    # PDF skaitomas vaiko procese, AI kvietimai lėti (~10-20 s), todėl lygiagrečiai; tvarka išlieka pagal failo vardą.
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as pool:
        results = list(pool.map(safe_parse, pdfs))
    for pdf, p in zip(pdfs, results):
        if isinstance(p, pdf_safe.PdfUnsafe):
            findings.append(Finding("invoices", "error", pdf_safe.MSG_UNSAFE,
                                    f"{pdf.name}: PDF viršijo saugos ribas (laikas, atmintis, puslapiai ar tekstas) "
                                    "arba yra sugadintas; neapdorotas.", ref=pdf.name,
                                    suggested_action="Nevesti į apskaitą; paprašyti tiekėjo atsiųsti sąskaitą iš naujo."))
            continue
        if isinstance(p, Exception):
            findings.append(Finding("invoices", "error", "Nepavyko perskaityti PDF",
                                    f"{pdf.name}: {p}", ref=pdf.name,
                                    suggested_action="Patikrinti failą arba paprašyti tiekėjo atsiųsti iš naujo."))
            continue
        if only_company and not is_for_company(p.text):
            findings.append(Finding("invoices", "info", MSG_NOT_COMPANY,
                                    f"{pdf.name}: pirkėjas ne UAB Uno parts (nėra kodo {COMPANY_CODE}); "
                                    "į sąrašą ir Rivilės failą neįtraukta.", ref=pdf.name,
                                    suggested_action="Nieko daryti nereikia; nepersiųsti kitų įmonių sąskaitų."))
            continue
        parsed.append(p)
        findings += _check_missing(p) + _check_supplier(p) + _check_arithmetic(p) + _check_line_totals(p)
    findings += _check_duplicates(parsed)
    return parsed, findings
