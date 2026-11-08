"""Patvirtinimai: galioja tik tol, kol nepasikeitė visa modulio įvestis ir taisyklių versija."""
from __future__ import annotations

import hashlib

from core.store import Store

# Visi failų tipai, nuo kurių priklauso modulio rezultatas (įskaitant neprivalomus).
MODULE_KINDS = {
    "timesheet": ("employees", "timesheet", "rivile_hours", "sick_certs", "vacation_requests"),
}


class StaleRunError(Exception):
    """Skaičiavimas nebeatitinka dabartinių įkėlimų arba taisyklių versijos."""


def _inputs_hash(store: Store, company_id: int, upload_ids: list[int]) -> str:
    """Įvesties santrauka iš įkeltų failų sha256 (pagal id tvarką)."""
    hashes = []
    for upload_id in sorted(upload_ids):
        upload = store.get_upload(company_id, upload_id)
        if upload is None:
            raise KeyError(f"Įkėlimas {upload_id} nerastas šiai įmonei")
        hashes.append(upload["sha256"])
    return hashlib.sha256("|".join(hashes).encode()).hexdigest()


def _used_by_kind(store: Store, company_id: int, input_ids: list[int]) -> dict[str, int]:
    used: dict[str, int] = {}
    for upload_id in input_ids:
        upload = store.get_upload(company_id, upload_id)
        if upload is None:
            raise KeyError(f"Įkėlimas {upload_id} nerastas šiai įmonei")
        used[upload["kind"]] = max(used.get(upload["kind"], 0), upload_id)
    return used


def changed_input_kinds(store: Store, company_id: int, period: str, module: str, input_ids: list[int]) -> list[str]:
    """Failų tipai, kurių naujausias įkėlimas nebesutampa su naudotu (arba abu nebūti)."""
    used = _used_by_kind(store, company_id, input_ids)
    changed = []
    for kind in MODULE_KINDS.get(module, tuple(used)):
        uploads = store.list_uploads(company_id, str(period), kind)
        latest = max((u["id"] for u in uploads), default=None)
        if latest != used.get(kind):
            changed.append(kind)
    return changed


def _input_set_unchanged(store: Store, company_id: int, period: str, module: str, input_ids: list[int]) -> bool:
    return not changed_input_kinds(store, company_id, period, module, input_ids)


def is_run_fresh(store: Store, company_id: int, period: str, module: str, run: dict,
                 rules_version: str | None = None) -> bool:
    if rules_version is not None and run["rules_version"] != rules_version:
        return False
    try:
        return _input_set_unchanged(store, company_id, period, module, run["input_upload_ids"])
    except KeyError:
        return False


def record_run(store: Store, company_id: int, period: str, module: str, rules_version: str,
               input_upload_ids: list[int], result, user: str) -> int:
    """Išsaugo skaičiavimą ir įrašo auditą."""
    run_id = store.save_run(company_id, period, module, rules_version, input_upload_ids, result)
    store.log(company_id, user, "run", f"{module} {period} run#{run_id} v{rules_version}", period)
    return run_id


def approve(store: Store, company_id: int, period: str, module: str, run_id: int, user: str,
            rules_version: str | None = None) -> int:
    """Patvirtina tik šviežią skaičiavimą: pasenusiam patvirtinimo eilutė net nekuriama."""
    run = store.get_run(company_id, run_id)
    if run is None or run["period"] != str(period) or run["module"] != module:
        raise ValueError("Skaičiavimas nerastas šiai įmonei / laikotarpiui / modulio")
    if not is_run_fresh(store, company_id, str(period), module, run, rules_version):
        raise StaleRunError(f"Skaičiavimas #{run_id} pasenęs")
    digest = _inputs_hash(store, company_id, run["input_upload_ids"])
    approval_id = store.add_approval(company_id, period, module, run_id, digest, user)
    store.log(company_id, user, "approve", f"{module} {period} run#{run_id} {digest[:12]}", period)
    return approval_id


def is_approved(store: Store, company_id: int, period: str, module: str,
                rules_version: str | None = None) -> bool:
    approval = store.active_approval(company_id, period, module)
    if approval is None:
        return False
    run = store.get_run(company_id, approval["run_id"])
    if run is None or not is_run_fresh(store, company_id, str(period), module, run, rules_version):
        return False
    try:
        return _inputs_hash(store, company_id, run["input_upload_ids"]) == approval["input_sha256"]
    except KeyError:
        return False


def revoke(store: Store, company_id: int, period: str, module: str, user: str) -> int:
    count = store.revoke_approvals(company_id, period, module)
    store.log(company_id, user, "revoke", f"{module} {period} ({count})", period)
    return count
