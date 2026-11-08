"""Rivilė GAMA pirkimo sąskaitų importo failas (EIP, XML).

GAMA: Pirkimai -> Pirkimo dokumentai -> Importas -> Išorinis importas (EIP failas).
Žymių vardai = GAMA lentelių stulpeliai: I06_PARH (antraštė) su įdėtais I07_PARD (eilutės) ir I08 (apmokėjimo terminas).
Tyrimas ir kas patikrinta / ne: .flow/2026-11-08-gama-eip/RESEARCH.md.

Kodai (tipas, mokesčio kodas, paslaugų kodas, sandėlis) imami iš UNO GAMA realių dokumentų
(tools/gama_purchase_refs.ps1 -> UNO_DATA\\gama_purchase_refs.json); jei failo nėra, naudojami DEFAULT_REFS.
Į GAMA DB niekas nerašoma: tik failas, kurį importuoja buhalterė.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from core.invoices import ParsedInvoice
from core.models import InvoiceLine, money

ENCODING = "windows-1257"   # FinAI/Invox: tokią koduotę skaito Rivilė (nepatikrinta prieš tikrą GAMA)
REFS_FILENAME = "gama_purchase_refs.json"

# Realūs UNO GAMA duomenys (2026-11-08, žr. RESEARCH.md): 1833 pirkimo dokumentai su PVM, tipas 1 (Važtaraštis),
# eilutės I07_TIPAS=3 (kodas iš N25_KOD), sandėlis ADMIN, matas VNT, mokestis A (I07_MOKESTIS=1) + procentas.
DEFAULT_REFS: dict = {
    "root": "",                     # Rivilės B00_IMP žurnale EIP įrašai be šakninio elemento ir be XML deklaracijos
    "declaration": False,
    "op_tip": "1",                  # I06_OP_TIP pirkimai: 1 Važtaraštis, 2 Grąžinimas
    "line_tipas": "3",              # I07_TIPAS: 1 prekė, 2 paslauga, 3 kodas (N25)
    "tax_code": "PVM",              # I06_KODAS_XS kai yra PVM
    "tax_code_zero": "PVM_0",       # I06_KODAS_XS kai visos eilutės 0 %
    "tax_index": "1",               # I07_MOKESTIS (A = 21 % stulpelis; procentas perrašomas I07_MOKESTIS_P)
    "warehouse": "ADMIN",           # I07_KODAS_IS
    "unit": "VNT",                  # I07_KODAS_US
    "default_code": "63027",        # N25 'Kitos sąnaudos'
    "codes": ["63021", "63022", "63023", "63024", "63025", "63026", "63027", "63028", "63030", "63031", "63032",
              "63033", "63034", "63035", "63036", "63042", "63044", "6205", "6312"],
    "vat_klasi": {"21": "PVM1", "9": "PVM2", "5": "PVM3"},   # 0 % palieka tuščią: klasifikatorių parenka buhalterė
}


@dataclass
class EipResult:
    xml: bytes
    included: list[str] = field(default_factory=list)   # sąskaitų numeriai
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (numeris, priežastis)


def load_refs(path: Path | None = None) -> dict:
    """DEFAULT_REFS + UNO_DATA\\gama_purchase_refs.json (jei yra), išvestos reikšmės iš realių dokumentų."""
    refs = {**DEFAULT_REFS, "vat_klasi": dict(DEFAULT_REFS["vat_klasi"])}
    if path is None:
        from core import suppliers
        path = suppliers.store_path().with_name(REFS_FILENAME)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))["data"]
    except (OSError, ValueError, KeyError, TypeError):
        return refs
    purchase_codes = [c["code"] for c in raw.get("codes", []) if c.get("tipas") == "1" and c.get("code")]
    if purchase_codes:
        refs["codes"] = purchase_codes
    lines = [x for x in raw.get("line_combos", []) if x.get("tipas") == "3"]
    for key, field_ in (("warehouse", "warehouse"), ("unit", "unit")):
        top = Counter()
        for x in lines:
            if x.get(field_):
                top[x[field_]] += int(x.get("n") or 0)
        if top:
            refs[key] = top.most_common(1)[0][0]
    if refs["default_code"] not in refs["codes"] and refs["codes"]:
        refs["default_code"] = refs["codes"][0]
    return refs


def _num(value: Decimal, places: int = 2) -> str:
    return f"{value:.{places}f}"


def _qty(q: Decimal) -> str:
    return format(q.normalize(), "f") if q != q.to_integral() else str(int(q))


def _date(d: date) -> str:
    return d.strftime("%Y.%m.%d")


def _text(value: str, limit: int | None = None) -> str:
    value = " ".join((value or "").split())
    if limit:
        value = value[:limit]
    return escape(value)


def _tag(name: str, value: str) -> str:
    return f"<{name}>{value}</{name}>"


def _pct(rate: Decimal) -> Decimal:
    return (rate * 100).quantize(Decimal("0.01"))


def _klasi(refs: dict, rate: Decimal) -> str:
    key = str(int(_pct(rate))) if _pct(rate) == _pct(rate).to_integral() else str(_pct(rate))
    return refs["vat_klasi"].get(key, "")


def _service_code(refs: dict, supplier_account: str) -> str:
    return supplier_account if supplier_account in refs["codes"] else refs["default_code"]


def _line_xml(refs: dict, code: str, ln: InvoiceLine) -> str:
    pct = _pct(ln.vat_rate)
    tags = [
        ("I07_TIPAS", refs["line_tipas"]),
        ("I07_KODAS_IS", _text(refs["warehouse"])),
        ("I07_KODAS", _text(code)),
        ("I07_PAV", _text(ln.description, 40)),
        ("I07_KODAS_US", _text(refs["unit"])),
        ("T_KIEKIS", _qty(ln.quantity)),
    ]
    if ln.quantity == ln.quantity.to_integral():
        tags += [("I07_KIEKIS", _qty(ln.quantity)), ("I07_FRAKCIJA", "1")]
    tags += [
        ("I07_KAINA_BE", _num(ln.unit_price, 4)),
        ("I07_KAINA_SU", _num(ln.unit_price * (1 + ln.vat_rate), 4)),
        ("I07_MOKESTIS", refs["tax_index"]),
        ("I07_MOKESTIS_P", _num(pct)),
        ("I07_PVM", _num(ln.vat)),
        ("I07_SUMA", _num(ln.net)),
    ]
    kl = _klasi(refs, ln.vat_rate)
    if kl:
        tags.append(("I07_KODAS_KL", kl))
    tags.append(("I07_APRASYMAS1", _text(ln.description, 150)))
    return "<I07>" + "".join(_tag(n, v) for n, v in tags) + "</I07>"


def _document_xml(refs: dict, p: ParsedInvoice, account: str) -> str:
    code = _service_code(refs, account)
    net = money(sum((ln.net for ln in p.lines), Decimal(0)))
    vat = money(sum((ln.vat for ln in p.lines), Decimal(0)))
    taxed = any(ln.vat_rate > 0 for ln in p.lines)
    days = max((p.due_date - p.issue_date).days, 0)
    head = [
        ("I06_KODAS_KS", _text(p.supplier_code)),
        ("I06_OP_TIP", refs["op_tip"]),
        ("I06_PVM_TIP", "0"),
        ("I06_DOK_NR", _text(p.number, 20)),
        ("I06_OP_DATA", _date(p.issue_date)),
        ("I06_DOK_DATA", _date(p.issue_date)),
        ("I06_KODAS_XS", _text(refs["tax_code"] if taxed else refs["tax_code_zero"])),
        ("I06_VAL_POZ", "0"),
        ("I06_PVM_SKOL", "1"),
        ("I06_MOK_DOK", _text(p.number, 12)),
        ("I06_PASTABOS", _text(f"UNO imp. viso {money(net + vat)} EUR", 40)),
        ("I06_APRASYMAS1", _text(f"UNO importas. Be PVM {net}, PVM {vat}, su PVM {money(net + vat)} EUR. "
                                 f"Failas: {Path(p.source_file).name}", 150)),
    ]
    body = "".join(_line_xml(refs, code, ln) for ln in p.lines)
    terms = "<I08>" + _tag("I08_EIL_NR", "1") + _tag("I08_MOK_P", "100.00000") + _tag("I08_MOK_D", str(days)) + "</I08>"
    return "<I06>" + "".join(_tag(n, v) for n, v in head) + body + terms + "</I06>"


INDENT = "     "   # kaip Rivilės pačios EIP įrašuose (B00_IMP): viena žymė eilutėje, 5 tarpai lygiui
CRLF = chr(13) + chr(10)


def _pretty(xml: str) -> str:
    """<I06><A>1</A><I07>..</I07></I06> -> viena žymė eilutėje su įtrauka (Rivilės EIP stilius)."""
    from xml.etree import ElementTree

    lines: list[str] = []

    def walk(el, depth: int) -> None:
        pad = INDENT * depth
        if len(el):
            lines.append(f"{pad}<{el.tag}>")
            for child in el:
                walk(child, depth + 1)
            lines.append(f"{pad}</{el.tag}>")
        else:
            lines.append(f"{pad}<{el.tag}>{escape(el.text or '')}</{el.tag}>")

    walk(ElementTree.fromstring(xml), 0)
    return CRLF.join(lines)




def _skip_reason(p: ParsedInvoice) -> str:
    if not p.supplier_code:
        return "nėra tiekėjo kodo GAMA kartotekoje"
    if not p.lines:
        return "nėra eilučių"
    if not (p.number and p.issue_date and p.due_date):
        return "trūksta numerio ar datų"
    return ""


def build_eip(parsed: list[ParsedInvoice], refs: dict | None = None) -> EipResult:
    from core import suppliers

    refs = refs or load_refs()
    by_code = {s.rivile_code: s for s in suppliers.all_suppliers()}
    blocks: list[str] = []
    result = EipResult(xml=b"")
    for p in parsed:
        reason = _skip_reason(p)
        if reason:
            result.skipped.append((p.number, reason))
            continue
        sup = by_code.get(p.supplier_code)
        blocks.append(_document_xml(refs, p, sup.expense_account if sup else ""))
        result.included.append(p.number)
    root = refs.get("root", "")
    inner = "\r\n".join(_pretty(b) for b in blocks)
    body = f"<{root}>\r\n{inner}\r\n</{root}>" if root else inner
    head = f'<?xml version="1.0" encoding="{ENCODING}"?>\r\n' if refs.get("declaration") else ""
    text = f"{head}{body}\r\n"
    result.xml = text.encode(ENCODING, errors="xmlcharrefreplace")
    return result


def purchases_to_eip(parsed: list[ParsedInvoice], path: Path, refs: dict | None = None) -> Path:
    result = build_eip(parsed, refs)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(result.xml)
    return Path(path)


__all__ = ["DEFAULT_REFS", "EipResult", "build_eip", "load_refs", "purchases_to_eip"]
