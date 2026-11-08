"""Generuoja 60 pirkimo sąskaitų PDF (spalio mėn.) iš world.PURCHASE_INVOICES.

Paleidimas iš projekto šaknies: py -3.12 -m data.gen_invoices
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from core import world
from core.invoices import CLEAN_LAYOUT, DocData, Layout, Party, draw_invoice

DEFAULT_OUT = Path(__file__).resolve().parent / "generated" / "saskaitos"
VAT_ERROR_DELTA = Decimal("3.40")

# Trys skirtingi tiekėjų maketai (kitos etiketės, tvarka, datų formatas)
LAYOUT_A = Layout(
    title="SĄSKAITA FAKTŪRA", number_label="Sąskaitos Nr.", issue_label="Išrašymo data",
    due_label="Apmokėti iki", seller_label="Pardavėjas", buyer_label="Pirkėjas",
    code_label="Įmonės kodas", vat_label="PVM kodas", iban_label="IBAN",
    cols=("Aprašymas", "Kiekis", "Kaina be PVM", "PVM", "Suma be PVM"),
    net_label="Suma be PVM", vat_total_label="PVM 21 %", gross_label="Suma su PVM",
)
LAYOUT_B = Layout(
    title=None, number_label="PVM sąskaita faktūra Serija/Nr.", issue_label="Data",
    due_label="Mokėjimo terminas", seller_label="Tiekėjas", buyer_label="Gavėjas",
    code_label="Įm. kodas", vat_label="PVM mokėtojo kodas", iban_label="Banko sąsk.",
    cols=("Prekė / paslauga", "Kiekis", "Vnt. kaina", "PVM", "Suma"),
    net_label="Iš viso be PVM", vat_total_label="PVM suma", gross_label="Iš viso mokėti",
    date_fmt="%Y.%m.%d", buyer_first=True, name_inline=False,
)
LAYOUT_C = Layout(
    title="MOKĖJIMO DOKUMENTAS", number_label="Dokumento Nr.", issue_label="Data",
    due_label="Apmokėjimo terminas", seller_label="Siuntėjas", buyer_label="Užsakovas",
    code_label="Kodas", vat_label="PVM Nr.", iban_label="A/s",
    cols=("Pavadinimas", "Kiek.", "Kaina", "PVM", "Viso"),
    net_label="Bendra suma be PVM", vat_total_label="PVM (21 %)", gross_label="Bendra suma mokėti",
    date_fmt="%d.%m.%Y", meta_last=True,
)
LAYOUTS = [LAYOUT_A, LAYOUT_B, LAYOUT_C]


def layout_for(supplier_code: str) -> Layout:
    """Tas pats tiekėjas visada naudoja tą patį maketą."""
    return LAYOUTS[(int(supplier_code[-3:]) - 1) % len(LAYOUTS)]


def build_doc(idx: int) -> DocData:
    inv = world.PURCHASE_INVOICES[idx]
    sup = world.supplier_by_code(inv.supplier_code)
    vat = inv.vat
    if idx == world.PDF_VAT_ERROR_IDX:
        vat = vat + VAT_ERROR_DELTA   # sąmoninga klaida PDF'e; bendra suma spausdinama pagal klaidingą PVM
    c = world.COMPANY
    return DocData(
        number=inv.number, issue=inv.issue_date, due=inv.due_date,
        seller=Party(sup.name, sup.company_code, sup.vat_code, sup.iban),
        buyer=Party(c.name, c.code, c.vat_code, c.iban, c.address),
        lines=inv.lines, net=inv.net, vat=vat, gross=inv.net + vat,
    )


def main(out_dir: Path | None = None) -> list[Path]:
    out = Path(out_dir) if out_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, inv in enumerate(world.PURCHASE_INVOICES):
        path = out / f"{idx + 1:02d}_{inv.number}.pdf"
        draw_invoice(path, layout_for(inv.supplier_code), build_doc(idx))
        paths.append(path)
    print(f"Sugeneruota {len(paths)} sąskaitų: {out}")
    return paths


__all__ = ["main", "LAYOUTS", "layout_for", "build_doc", "CLEAN_LAYOUT"]

if __name__ == "__main__":
    main()
