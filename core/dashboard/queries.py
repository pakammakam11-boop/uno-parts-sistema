"""SQL užklausos faktų lentelėms (demo jungtys naudoja jas; tikros jungtys užpildytų tas pačias lenteles)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from core.dashboard.schema import (dash_budget, dash_cost_lines, dash_deposits, dash_ops_daily, dash_sales_daily,
                                   dash_work_items)

SALES_SUMS = ("revenue", "cogs", "orders", "lines", "returns_count", "returns_amount")


def _where(table, start: date, end: date, branch: str) -> list:
    conds = [table.c.day >= start, table.c.day <= end]
    if branch != "all":
        conds.append(table.c.branch == branch)
    return conds


def _sales_dict(row) -> dict:
    out = {k: float(row[k] or 0) for k in SALES_SUMS}
    for k in ("orders", "lines", "returns_count"):
        out[k] = int(out[k])
    out["gross_profit"] = out["revenue"] - out["cogs"]
    return out


def _sales_cols(t):
    return [func.sum(getattr(t.c, k)).label(k) for k in SALES_SUMS]


def sales_totals(conn, start: date, end: date, branch: str) -> dict:
    t = dash_sales_daily
    row = conn.execute(select(*_sales_cols(t)).where(*_where(t, start, end, branch))).mappings().one()
    return _sales_dict(row)


def sales_daily(conn, start: date, end: date, branch: str) -> list[dict]:
    t = dash_sales_daily
    rows = conn.execute(select(t.c.day, *_sales_cols(t)).where(*_where(t, start, end, branch))
                        .group_by(t.c.day).order_by(t.c.day)).mappings().all()
    return [{"day": r["day"], **_sales_dict(r)} for r in rows]


def sales_by(conn, column: str, start: date, end: date, branch: str) -> list[dict]:
    """Pardavimai pagal manufacturer arba branch."""
    t = dash_sales_daily
    col = getattr(t.c, column)
    rows = conn.execute(select(col.label("key"), *_sales_cols(t)).where(*_where(t, start, end, branch))
                        .group_by(col)).mappings().all()
    return [{"key": r["key"], **_sales_dict(r)} for r in rows]


def cost_by_group(conn, start: date, end: date, branch: str) -> dict[str, dict]:
    t = dash_cost_lines
    rows = conn.execute(select(t.c.grp, func.sum(t.c.amount).label("amount"), func.count().label("docs"),
                               func.count(func.distinct(t.c.supplier)).label("suppliers"))
                        .where(*_where(t, start, end, branch)).group_by(t.c.grp)).mappings().all()
    return {r["grp"]: {"amount": float(r["amount"]), "docs": r["docs"], "suppliers": r["suppliers"]} for r in rows}


def cost_sources(conn, start: date, end: date, branch: str) -> dict[str, set[str]]:
    t = dash_cost_lines
    out: dict[str, set[str]] = {}
    for grp, source in conn.execute(select(t.c.grp, t.c.source).where(*_where(t, start, end, branch)).distinct()):
        out.setdefault(grp, set()).add(source)
    return out


def cost_by_supplier(conn, group: str, start: date, end: date, branch: str) -> dict[str, dict]:
    t = dash_cost_lines
    rows = conn.execute(select(t.c.supplier, func.sum(t.c.amount).label("amount"), func.count().label("docs"))
                        .where(*_where(t, start, end, branch), t.c.grp == group)
                        .group_by(t.c.supplier)).mappings().all()
    return {r["supplier"]: {"amount": float(r["amount"]), "docs": r["docs"]} for r in rows}


def cost_documents(conn, group: str, supplier: str, start: date, end: date, branch: str) -> list[dict]:
    t = dash_cost_lines
    rows = conn.execute(select(t.c.day, t.c.doc_no, t.c.amount, t.c.branch, t.c.source)
                        .where(*_where(t, start, end, branch), t.c.grp == group, t.c.supplier == supplier)
                        .order_by(t.c.day.desc(), t.c.doc_no.desc())).mappings().all()
    return [dict(r) for r in rows]


def cost_amount(conn, start: date, end: date, branch: str, group: str | None = None,
                suppliers: tuple[str, ...] | None = None, source: str | None = None) -> float:
    t = dash_cost_lines
    conds = _where(t, start, end, branch)
    if group:
        conds.append(t.c.grp == group)
    if suppliers:
        conds.append(t.c.supplier.in_(suppliers))
    if source:
        conds.append(t.c.source == source)
    return float(conn.execute(select(func.coalesce(func.sum(t.c.amount), 0)).where(*conds)).scalar())


def ops_daily(conn, start: date, end: date, branch: str) -> list[dict]:
    t = dash_ops_daily
    rows = conn.execute(select(t.c.day, func.sum(t.c.calls_total).label("calls_total"),
                               func.sum(t.c.missed_calls).label("missed_calls"), func.sum(t.c.stops).label("stops"),
                               func.sum(t.c.km).label("km"), func.sum(t.c.late_stops).label("late_stops"))
                        .where(*_where(t, start, end, branch)).group_by(t.c.day).order_by(t.c.day)).mappings().all()
    return [{"day": r["day"], **{k: float(r[k] or 0) if k == "km" else int(r[k] or 0)
                                 for k in ("calls_total", "missed_calls", "stops", "km", "late_stops")}} for r in rows]


def deposits(conn, branch: str) -> list[dict]:
    t = dash_deposits
    query = select(t).where(t.c.status == "waiting").order_by(t.c.created_day)
    if branch != "all":
        query = query.where(t.c.branch == branch)
    return [dict(r) for r in conn.execute(query).mappings()]


def work_items(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(select(dash_work_items).order_by(dash_work_items.c.id)).mappings()]


def budget(conn, month: str, branch: str) -> dict:
    t = dash_budget
    conds = [t.c.month == month] + ([t.c.branch == branch] if branch != "all" else [])
    row = conn.execute(select(func.coalesce(func.sum(t.c.revenue), 0).label("revenue"),
                              func.coalesce(func.sum(t.c.gross_profit), 0).label("gross_profit"))
                       .where(*conds)).mappings().one()
    return {"revenue": float(row["revenue"]), "gross_profit": float(row["gross_profit"])}
