"""Kaštų suvestinė ir išskleidimas: grupė -> tiekėjai -> dokumentai."""
from __future__ import annotations

import re
import unicodedata

from core.dashboard import connectors, queries
from core.dashboard import periods as per
from core.dashboard.generator import COST_GROUPS
from core.dashboard.metrics import money
from core.hr.errors import not_found


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def _line(amount: float, prev: float, total: float) -> dict:
    change = per.pct_change(amount, prev)
    return {"amount": money(amount), "prev_amount": money(prev), "prev": money(prev), "change_pct": change,
            "delta_pct": change, "share_pct": per.ratio_pct(amount, total)}


def _ranges(period: str) -> tuple[per.Range, per.Range]:
    rng = per.resolve(period, per.as_of_day())
    return rng, per.previous(period, rng)


def groups(conn, period: str, branch: str) -> dict:
    per.validate(period, branch)
    data = connectors.demo_connectors(conn)["rivile"].fetch(period, branch)
    total = sum(g["amount"] for g in data["groups"].values())
    prev_total = sum(g["amount"] for g in data["previous_groups"].values())
    items = []
    for gid, (label, _) in COST_GROUPS.items():
        cur, prev = data["groups"].get(gid, {}), data["previous_groups"].get(gid, {})
        items.append({"id": gid, "key": gid, "label": label, "source": COST_GROUPS[gid][1],
                      **_line(cur.get("amount", 0.0), prev.get("amount", 0.0), total),
                      "suppliers": cur.get("suppliers", 0), "documents": cur.get("docs", 0)})
    items.sort(key=lambda g: -g["amount"])
    return {"period": period, "branch": branch, "range": data["range"].as_dict(),
            "previous_range": data["previous_range"].as_dict(), "total": money(total), "prev_total": money(prev_total),
            "change_pct": per.pct_change(total, prev_total), "groups": items,
            **connectors.envelope(conn, ["rivile", "bankas", "intercars"])}


def _group_label(group: str) -> str:
    if group not in COST_GROUPS:
        raise not_found("Kaštų grupė nerasta")
    return COST_GROUPS[group][0]


def suppliers(conn, group: str, period: str, branch: str) -> dict:
    per.validate(period, branch)
    label = _group_label(group)
    rng, prev = _ranges(period)
    cur = queries.cost_by_supplier(conn, group, rng.start, rng.end, branch)
    old = queries.cost_by_supplier(conn, group, prev.start, prev.end, branch)
    total = sum(v["amount"] for v in cur.values())
    items = [{"key": slug(name), "supplier": name, **_line(v["amount"], old.get(name, {}).get("amount", 0.0), total),
              "name": name, "documents": v["docs"], "docs_count": v["docs"]} for name, v in cur.items()]
    items.sort(key=lambda s: -s["amount"])
    return {"group": {"id": group, "label": label}, "period": period, "branch": branch, "range": rng.as_dict(),
            "total": money(total), "suppliers": items, **connectors.envelope(conn, ["rivile", "bankas", "intercars"])}


def documents(conn, group: str, supplier_key: str, period: str, branch: str) -> dict:
    per.validate(period, branch)
    label = _group_label(group)
    rng, _ = _ranges(period)
    names = queries.cost_by_supplier(conn, group, rng.start, rng.end, branch)
    name = next((n for n in names if slug(n) == slug(supplier_key)), None)
    if name is None:
        raise not_found("Tiekėjas nerastas šiame laikotarpyje")
    rows = queries.cost_documents(conn, group, name, rng.start, rng.end, branch)
    docs = [{"date": r["day"].isoformat(), "doc_no": r["doc_no"], "supplier": name, "amount": money(r["amount"]),
             "branch": r["branch"], "branch_label": per.BRANCH_LABELS[r["branch"]], "source": r["source"],
             "number": r["doc_no"], "description": f"{name}, {per.BRANCH_LABELS[r['branch']]}"} for r in rows]
    return {"group": {"id": group, "label": label}, "supplier": {"key": slug(name), "name": name},
            "period": period, "branch": branch, "range": rng.as_dict(),
            "total": money(sum(d["amount"] for d in docs)), "documents": docs,
            **connectors.envelope(conn, ["rivile", "bankas", "intercars"])}
