"""Operacijos (skambučiai, depozitai, grąžinimai, maršrutai, HR, apskaita) ir Liutauro darbai (laiškai, užduotys)."""
from __future__ import annotations

from core.dashboard import actions, connectors
from core.dashboard import periods as per
from core.dashboard.metrics import money

PRIORITY_LABELS = {"high": "Aukštas", "normal": "Vidutinis", "low": "Žemas"}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
DEPOSIT_ITEMS = 8


def calls_block(tel: dict, avg_order: float) -> dict:
    series = [{"date": r["day"].isoformat(), "missed": r["missed_calls"], "total": r["calls_total"]} for r in tel["series"]]
    missed = sum(r["missed"] for r in series)
    last = tel["last_day"][0] if tel["last_day"] else None
    return {
        "series": series, "missed_total": missed, "calls_total": sum(r["total"] for r in series),
        "missed_pct": per.ratio_pct(missed, sum(r["total"] for r in series)),
        "lost_revenue": money(actions.lost_revenue(missed, avg_order)),
        "last_day": {"date": last["day"].isoformat(), "missed": last["missed_calls"], "total": last["calls_total"],
                     "lost_revenue": money(actions.lost_revenue(last["missed_calls"], avg_order))} if last else None,
        "assumptions": {"conversion_pct": actions.CALL_CONVERSION * 100, "avg_order": money(avg_order),
                        "formula": "praleisti skambučiai x konversija x vidutinis užsakymas",
                        "note": "Prarasta apyvarta yra prielaida, ne faktinis nuostolis; vidutinis užsakymas iš "
                                "paskutinių 28 prekybos dienų pardavimų."},
    }


def deposits_block(rows: list[dict], today) -> dict:
    items = [{"id": d["id"], "supplier": d["supplier"], "branch": d["branch"], "qty": d["qty"], "value": money(d["value"]),
              "created": d["created_day"].isoformat(), "age_days": (today - d["created_day"]).days} for d in rows]
    old = [d for d in items if d["age_days"] > actions.DEPOSIT_OLD_DAYS]
    return {"waiting_count": len(items), "waiting_value": money(sum(d["value"] for d in items)),
            "over_30d": {"count": len(old), "value": money(sum(d["value"] for d in old))},
            "items": sorted(items, key=lambda d: -d["age_days"])[:DEPOSIT_ITEMS]}


def routes_block(rows: list[dict]) -> dict:
    stops, km, late = sum(r["stops"] for r in rows), sum(r["km"] for r in rows), sum(r["late_stops"] for r in rows)
    return {"stops": stops, "km": round(km, 1), "late_stops": late, "late_pct": per.ratio_pct(late, stops),
            "km_per_stop": round(km / stops, 1) if stops else None, "days": len(rows)}


def operations(conn, period: str, branch: str) -> dict:
    per.validate(period, branch)
    c = connectors.demo_connectors(conn)
    inter, tel, riv = c["intercars"].fetch(period, branch), c["telefonija"].fetch(period, branch), c["rivile"].fetch(period, branch)
    avg_order = actions.avg_order_value(inter["daily"])
    cur = inter["current"]
    env = lambda *ids: connectors.envelope(conn, list(ids))  # noqa: E731
    calls, deposits, routes = calls_block(tel, avg_order), deposits_block(riv["deposits"], per.now_local().date()), routes_block(inter["routes"])
    hr, acc = c["personalas"].fetch(period, branch), c["apskaita"].fetch(period, branch)
    return {
        "period": period, "branch": branch, "range": inter["range"].as_dict(),
        "calls": {**calls, "missed": calls["missed_total"], "conversion": calls["assumptions"]["conversion_pct"],
                  "missed_yesterday": calls["last_day"]["missed"] if calls["last_day"] else 0,
                  "avg_order": calls["assumptions"]["avg_order"], **env("telefonija")},
        "deposits": {**deposits, "count": deposits["waiting_count"], "value": deposits["waiting_value"],
                     "overdue_count": deposits["over_30d"]["count"], "overdue_value": deposits["over_30d"]["value"],
                     "overdue_days": actions.DEPOSIT_OLD_DAYS, **env("rivile")},
        "returns": {"count": cur["returns_count"], "amount": money(cur["returns_amount"]),
                    "value": money(cur["returns_amount"]),
                    "pct": per.ratio_pct(cur["returns_amount"], cur["revenue"]),
                    "pct_of_revenue": per.ratio_pct(cur["returns_amount"], cur["revenue"]),
                    "pct_of_orders": per.ratio_pct(cur["returns_count"], cur["orders"]), **env("intercars")},
        "routes": {**routes, "late": routes["late_stops"],
                   "on_time_pct": round(100 - routes["late_pct"], 1) if routes["late_pct"] is not None else None,
                   **env("intercars")},
        "hr": {**hr, "pending_signatures": len(hr["pending_for_director"]),
               "on_vacation": hr["on_leave_this_week"], **env("personalas")},
        "accounting": {**acc, "held_invoices": acc["invoices_held"], "close_errors": acc["month_close_errors"],
                       **env("apskaita")},
        **env("intercars", "telefonija", "rivile", "personalas", "apskaita"),
    }


def work(conn) -> dict:
    data = connectors.demo_connectors(conn)["pastas"].fetch("day", "all")
    mails = sorted(({"id": m["id"], "priority": m["priority"], "priority_label": PRIORITY_LABELS[m["priority"]],
                     "sender": m["counterparty"], "sender_kind": m["category"], "subject": m["title"],
                     "mailbox": m["mailbox"], "received_at": m["received_at"].isoformat(timespec="minutes"),
                     "waiting_hours": m["waiting_hours"]} for m in data["mails"]),
                   key=lambda m: (PRIORITY_ORDER[m["priority"]], -m["waiting_hours"]))
    tasks = sorted(({"id": t["id"], "priority": t["priority"], "priority_label": PRIORITY_LABELS[t["priority"]],
                     "title": t["title"], "due": t["due_day"].isoformat(), "days_left": t["days_left"],
                     "overdue": t["days_left"] < 0} for t in data["tasks"]),
                   key=lambda t: (t["days_left"], PRIORITY_ORDER[t["priority"]]))
    emails = [{**m, "from": m["sender"]} for m in mails]
    tasks = [{**t, "done": False} for t in tasks]
    return {"emails": emails, "mails": emails, "tasks": tasks,
            "counts": {"mails": len(mails), "high": sum(1 for m in mails if m["priority"] == "high"),
                       "tasks": len(tasks), "overdue": sum(1 for t in tasks if t["overdue"])},
            **connectors.envelope(conn, ["pastas"])}
