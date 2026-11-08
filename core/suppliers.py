"""Tiekėjų kartoteka: demo tiekėjai (world.SUPPLIERS) + naudotojo sukurti (JSON failas).

Naują tiekėją buhalterė sukuria iš radinio „Nežinomas tiekėjas“; jis keliauja į Rivilės
importo lapą 'Clients', todėl Rivilėje kortelė atsiranda kartu su sąskaita.
Failas: UNO_SUPPLIERS_FILE, numatyta šalia pašto aplanko (UNO_DATA) arba data/suppliers.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict
from pathlib import Path

from core import world
from core.models import Supplier

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPENSE_ACCOUNT = "6000"
CODE_PREFIX = "TIEK"
FIRST_NEW_NUMBER = 101
_lock = threading.Lock()


def store_path() -> Path:
    from core.mail_import import _env  # tas pats env + Windows registro kelias

    explicit = _env("UNO_SUPPLIERS_FILE")
    if explicit:
        return Path(explicit)
    mail_dir = _env("UNO_MAIL_DIR")
    if mail_dir:
        return Path(mail_dir).parent / "suppliers.json"
    return ROOT / "data" / "suppliers.json"


def _load() -> list[Supplier]:
    path = store_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    fields = Supplier.__dataclass_fields__
    return [Supplier(**{k: str(item.get(k, "")) for k in fields}) for item in raw if isinstance(item, dict)]


def all_suppliers() -> list[Supplier]:
    return [*world.SUPPLIERS, *_load()]


def norm_name(name: str) -> str:
    return re.sub(r"[^0-9a-ząčęėįšųūž]+", "", (name or "").lower())


def find(company_code: str = "", vat_code: str = "", name: str = "") -> Supplier | None:
    """Pagal įmonės kodą, tada PVM kodą, tada (tik be kodų) pagal pavadinimą."""
    items = all_suppliers()
    for attr, value in (("company_code", company_code), ("vat_code", vat_code)):
        value = (value or "").strip()
        if value:
            hit = next((s for s in items if getattr(s, attr) == value), None)
            if hit:
                return hit
    key = norm_name(name)
    if key and not (company_code or vat_code):
        return next((s for s in items if norm_name(s.name) == key), None)
    return None


def add(name: str, company_code: str = "", vat_code: str = "", iban: str = "",
        expense_account: str = DEFAULT_EXPENSE_ACCOUNT) -> tuple[Supplier, bool]:
    """Sukuria tiekėją. Grąžina (tiekėjas, ar naujas); jei toks jau yra, grąžina esamą."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Tiekėjo pavadinimas privalomas.")
    with _lock:
        existing = find(company_code, vat_code, name)
        if existing:
            return existing, False
        created = _load()
        used = {int(s.rivile_code[len(CODE_PREFIX):]) for s in created
                if s.rivile_code.startswith(CODE_PREFIX) and s.rivile_code[len(CODE_PREFIX):].isdigit()}
        number = max(used | {FIRST_NEW_NUMBER - 1}) + 1
        sup = Supplier(f"{CODE_PREFIX}{number:03d}", name, (company_code or "").strip(), (vat_code or "").strip(),
                       (iban or "").strip(), "", expense_account or DEFAULT_EXPENSE_ACCOUNT)
        path = store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(s) for s in [*created, sup]], ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return sup, True
