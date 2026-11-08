"""Rivilė ERP XLSX (pirkimai ir pardavimai).

Faktai (research/rivile_erp_import.md): Rivilė ERP neturi dokumentuoto pilno pirkimo sąskaitos
XLSX importo. XLSX importas įkelia TIK eilutes į jau sukurtą dokumentą (lapas
'Purchase_invoice_lines'); tiekėjai importuojami lape 'Clients'. Dokumento antraštės lapas
'Antrastes' skirtas antraštei sukurti Rivilėje arba per Rivilė API (2 etapas).

Visi stulpelių pavadinimai yra COLUMNS žodyne: suderinus su atsisiųstu šablonu, keičiame tik čia.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from core import world
from core.invoices import ParsedInvoice
from core.models import Invoice, InvoiceLine, money

COLUMNS = {
    "ref": "##refId##",
    "code": "Code",
    "name": "Name",
    "reg_code": "RegCode",
    "vat_number": "VatNumber",
    "is_supplier": "IsSupplier",
    "doc_number": "DocumentNumber",
    "supplier_code": "SupplierCode",
    "client_code": "ClientCode",
    "doc_date": "DocumentDate",
    "due_date": "DueDate",
    "currency": "Currency",
    "amount_net": "AmountNet",
    "amount_vat": "AmountVat",
    "amount_gross": "AmountGross",
    "vat_code": "VatCode",
    "expense_account": "ExpenseAccount",
    "income_account": "IncomeAccount",
    # Rivilė 'Purchase_invoice_lines' stulpeliai (dokumentuota tvarka)
    "department": "DepartmentCode",
    "discount": "DiscountPerc",
    "item_code": "ItemCode",
    "item_name": "ItemName",
    "lot": "LotCode",
    "mark": "MarkCode",
    "notes": "Notes",
    "object": "ObjectCode",
    "price": "Price",
    "quantity": "Quantity",
    "serial": "Serial",
    "unit": "UnitOfMeasureCode",
}

CLIENT_KEYS = ["ref", "code", "name", "reg_code", "vat_number", "is_supplier"]
PURCHASE_HEADER_KEYS = ["doc_number", "supplier_code", "doc_date", "due_date", "currency",
                        "amount_net", "amount_vat", "amount_gross", "vat_code", "expense_account"]
SALES_HEADER_KEYS = ["doc_number", "client_code", "doc_date", "due_date", "currency",
                     "amount_net", "amount_vat", "amount_gross", "vat_code", "income_account"]
# doc_number paskutinis = pagalbinis stulpelis (prieš importą pašalinti)
LINE_KEYS = ["department", "discount", "item_code", "item_name", "lot", "mark", "notes", "object",
             "price", "quantity", "serial", "unit", "doc_number"]

# VMI: 21 % PVM1, 9 % PVM2, 0 % (atleista) PVM5; nuo 2026 egzistuoja ir 12 % PVM58.
VAT_CODES = {Decimal("0.21"): "PVM1", Decimal("0.09"): "PVM2", Decimal("0.00"): "PVM5",
             Decimal("0.12"): "PVM58"}
SALES_INCOME_ACCOUNT = "5001"
CURRENCY = "EUR"
DEPARTMENT = "UNO"   # demo padalinio kodas
UNIT = "VNT"
SOURCES = [
    "https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines",
    "https://guide.rivile.cloud/modules/purchase/duk/task_000012",
    "https://guide.rivile.cloud/description_module_windows/payment/payments",
]
INFO_ROWS = [
    "Rivilė ERP XLSX importas įkelia tik EILUTES į jau sukurtą pirkimo dokumentą (lapas 'Purchase_invoice_lines').",
    "Tiekėjai importuojami per lapą 'Clients' (##refId##, Code, Name, RegCode, VatNumber, IsSupplier).",
    "Lapas 'Antrastes' yra dokumentų antraštės (numeris, tiekėjas, datos, sumos, PVM kodas, sąnaudų sąskaita): "
    "pagal jį antraštė sukuriama Rivilėje arba per Rivilė API.",
    "Pilnas automatinis sąskaitų sukūrimas (antraštė + eilutės) = 2 etapas per Rivilė API.",
    "Eilučių importas: lape 'Purchase_invoice_lines' filtruoti pagal stulpelį DocumentNumber ir importuoti "
    "po vieną dokumentą; prieš importą pašalinti pagalbinį stulpelį DocumentNumber.",
    "Stulpelius ir lapo pavadinimą sutikrinti su atsisiųstu šablonu: Pirkimai → Pirkimų sąskaitos → "
    "Sukurti naują → Sąskaita → Importuoti → Atsisiųsti XLSX šabloną (dokumentacijoje lapo pavadinimas "
    "spausdinamas su tašku gale: išlaikyti tai, ką pateikia jūsų Rivilė).",
    "PVM kodai pagal VMI: 21 % PVM1; 9 % PVM2; 0 % (atleista) PVM5; nuo 2026 egzistuoja ir 12 % PVM58.",
    "Sąnaudų sąskaita imama iš tiekėjo kortelės; ItemCode = 'S' + sąnaudų sąskaita (demo).",
    "Antraštės sumos skaičiuojamos iš eilučių (kiekis x kaina), ne iš PDF atspausdintų sumų.",
]


def vat_code(rate: Decimal) -> str:
    return VAT_CODES.get(rate.quantize(Decimal("0.01")), "PVM?")


def ref_id(n: int) -> str:
    return f"REF{n:07d}"


def _put(ws: Worksheet, row: list) -> None:
    """Prideda eilutę; tekstas visada rašomas kaip tekstas (be formulių, pvz. '=1+1')."""
    ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
    for cell in ws[ws.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def _write_sheet(wb: Workbook, title: str, keys: list[str], rows: Iterable[list]) -> Worksheet:
    ws = wb.create_sheet(title)
    _put(ws, [COLUMNS[k] for k in keys])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        _put(ws, row)
    for idx, key in enumerate(keys, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = 16
        if key in ("doc_date", "due_date"):
            for col in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                for c in col:
                    c.number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"
    return ws


def _write_info(wb: Workbook) -> None:
    ws = wb.create_sheet("Info")
    _put(ws, ["Pastaba"])
    ws["A1"].font = Font(bold=True)
    for row in INFO_ROWS:
        _put(ws, [row])
    _put(ws, ["Šaltiniai"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    for url in SOURCES:
        _put(ws, [url])
    ws.column_dimensions["A"].width = 130


def _new_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def _save(wb: Workbook, path: Path) -> Path:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return Path(path)


def _totals(lines: Iterable[InvoiceLine]) -> tuple[Decimal, Decimal, Decimal]:
    lines = list(lines)
    net = money(sum((ln.net for ln in lines), Decimal(0)))
    vat = money(sum((ln.vat for ln in lines), Decimal(0)))
    return net, vat, money(net + vat)


def _vat_codes(lines: Iterable[InvoiceLine]) -> str:
    return "/".join(sorted({vat_code(ln.vat_rate) for ln in lines}))


def _line_row(account_prefix: str, account: str, ln: InvoiceLine, number: str) -> list:
    item = f"{account_prefix}{account}" if account else ""
    values = {"department": DEPARTMENT, "item_code": item, "item_name": ln.description,
              "notes": number, "price": ln.unit_price, "quantity": ln.quantity, "unit": UNIT,
              "doc_number": number}
    return [values.get(k, "") for k in LINE_KEYS]


def _supplier_of(p: ParsedInvoice):
    from core import suppliers

    return next((s for s in suppliers.all_suppliers() if s.rivile_code == p.supplier_code), None)


def _purchase_clients(parsed: list[ParsedInvoice]) -> list[list]:
    seen: dict[str, list] = {}
    for p in parsed:
        key = p.supplier_code or p.supplier_company_code or p.supplier_name
        if key in seen:
            continue
        sup = _supplier_of(p)
        name, reg, vat = (sup.name, sup.company_code, sup.vat_code) if sup else (
            p.supplier_name, p.supplier_company_code, p.supplier_vat_code)
        seen[key] = [ref_id(len(seen) + 1), p.supplier_code, name, reg, vat, 1]
    return list(seen.values())


def _purchase_rows(parsed: list[ParsedInvoice]) -> tuple[list[list], list[list]]:
    headers, lines = [], []
    for p in parsed:
        sup = _supplier_of(p)
        account = sup.expense_account if sup else ""
        net, vat, gross = _totals(p.lines)
        headers.append([p.number, p.supplier_code, p.issue_date, p.due_date, CURRENCY,
                        net, vat, gross, _vat_codes(p.lines), account])
        lines += [_line_row("S", account, ln, p.number) for ln in p.lines]
    return headers, lines


def purchases_to_xlsx(parsed: list[ParsedInvoice], path: Path) -> Path:
    headers, lines = _purchase_rows(parsed)
    wb = _new_workbook()
    _write_sheet(wb, "Clients", CLIENT_KEYS, _purchase_clients(parsed))
    _write_sheet(wb, "Antrastes", PURCHASE_HEADER_KEYS, headers)
    _write_sheet(wb, "Purchase_invoice_lines", LINE_KEYS, lines)
    _write_info(wb)
    return _save(wb, path)


def _client_codes(invoices: list[Invoice]) -> dict[str, str]:
    names = sorted({inv.buyer_name for inv in invoices})
    return {name: f"PIRK{i:03d}" for i, name in enumerate(names, start=1)}


def sales_to_xlsx(invoices: list[Invoice], path: Path) -> Path:
    codes = _client_codes(invoices)
    clients = [[ref_id(i), code, name, "", "", 0]
               for i, (name, code) in enumerate(codes.items(), start=1)]
    headers: list[list] = []
    lines: list[list] = []
    for inv in invoices:
        net, vat, gross = _totals(inv.lines)
        headers.append([inv.number, codes[inv.buyer_name], inv.issue_date, inv.due_date, CURRENCY,
                        net, vat, gross, _vat_codes(inv.lines), SALES_INCOME_ACCOUNT])
        lines += [_line_row("P", SALES_INCOME_ACCOUNT, ln, inv.number) for ln in inv.lines]
    wb = _new_workbook()
    _write_sheet(wb, "Clients", CLIENT_KEYS, clients)
    _write_sheet(wb, "Antrastes", SALES_HEADER_KEYS, headers)
    _write_sheet(wb, "Sales_invoice_lines", LINE_KEYS, lines)
    _write_info(wb)
    _put(wb["Info"], ["PIRK### pirkėjų kodai sugeneruoti demo tikslais; realiai imami iš Rivilė klientų kartotekos."])
    return _save(wb, path)


__all__ = ["COLUMNS", "purchases_to_xlsx", "sales_to_xlsx", "vat_code"]
