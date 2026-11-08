"""KPI, mėnesio prognozė ir pardavimų serija. Santykiai skaičiuojami iš sumų, ne iš vidurkių."""
from __future__ import annotations

from datetime import date, timedelta

from core.dashboard import actions, connectors, queries
from core.dashboard import periods as per
from core.hr import workdays

WEEKDAYS_LT = ["Pirmadienis", "Antradienis", "Trečiadienis", "Ketvirtadienis", "Penktadienis", "Šeštadienis", "Sekmadienis"]
FORECAST_WEEKS = 4
SERIES_SHORT_DAYS = 14
METHOD_TEXT = ("Mėnesio prognozė = faktas iki vakar + likusių prekybos dienų suma. Kiekvienos likusios dienos "
               "reikšmė = paskutinių 4 savaičių tos savaitės dienos vidurkis (valstybinių švenčių dienos į vidurkį "
               "neįtraukiamos, šventinės dienos prognozėje lygios 0, sekmadieniais neprekiaujama).")


def money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def eur_text(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " €"


def kpi(value: float, prev: float) -> dict:
    return {"value": money(value), "prev": money(prev), "change_pct": per.pct_change(value, prev)}


def weekday_averages(daily: list[dict], as_of: date, weeks: int = FORECAST_WEEKS) -> dict[int, dict]:
    """Paskutinių `weeks` prekybos dienų (kiekvienai savaitės dienai) pardavimų ir bendrojo pelno vidurkis."""
    by_day = {r["day"]: r for r in daily}
    out: dict[int, dict] = {}
    for weekday in range(6):
        days = [d for d in (as_of - timedelta(days=k) for k in range(connectors.FORECAST_LOOKBACK_DAYS)) if d.weekday() == weekday
                and per.is_trading_day(d) and d in by_day][:weeks]
        n = len(days)
        out[weekday] = {"revenue": sum(by_day[d]["revenue"] for d in days) / n if n else 0.0,
                        "gross_profit": sum(by_day[d]["gross_profit"] for d in days) / n if n else 0.0, "samples": n}
    return out


def forecast_days(avgs: dict[int, dict], as_of: date) -> list[dict]:
    """Likusių einamojo mėnesio (pagal šiandien) prekybos dienų prognozė (be sekmadienių ir LT švenčių)."""
    end = per.month_end(as_of + timedelta(days=1))
    return [{"date": d.isoformat(), "weekday": d.weekday(), "revenue": money(avgs[d.weekday()]["revenue"]),
             "gross_profit": money(avgs[d.weekday()]["gross_profit"])}
            for d in workdays.days_between(as_of + timedelta(days=1), end) if per.is_trading_day(d)]


def forecast_block(conn, branch: str, month_data: dict) -> dict:
    """Mėnesio prognozė ir palyginimas su biudžetu (pagal pasirinktą filialą)."""
    as_of = month_data["as_of"]
    today = as_of + timedelta(days=1)
    days = forecast_days(weekday_averages(month_data["daily"], as_of), as_of)
    actual, month = month_data["current"], f"{today.year}-{today.month:02d}"
    revenue = actual["revenue"] + sum(d["revenue"] for d in days)
    gross = actual["gross_profit"] + sum(d["gross_profit"] for d in days)
    budget = queries.budget(conn, month, branch)
    return {"month": month, "revenue_to_date": money(actual["revenue"]), "gross_profit_to_date": money(actual["gross_profit"]),
            "forecast_revenue": money(revenue), "forecast_gross_profit": money(gross),
            "budget_revenue": money(budget["revenue"]), "budget_gross_profit": money(budget["gross_profit"]),
            "vs_budget_pct": per.pct_change(revenue, budget["revenue"]),
            "progress_pct": per.ratio_pct(actual["revenue"], budget["revenue"]),
            "forecast_pct_of_budget": per.ratio_pct(revenue, budget["revenue"]),
            "remaining_trading_days": len(days), "days": days,
            "forecast": money(revenue), "budget": money(budget["revenue"]), "fact_to_date": money(actual["revenue"]),
            "pct_of_budget": per.ratio_pct(revenue, budget["revenue"]), "remaining_workdays": len(days),
            "method": METHOD_TEXT,
            "how": {"text": METHOD_TEXT, "as_of": as_of.isoformat(),
                    "weekday_averages": [{"weekday": WEEKDAYS_LT[w], "avg_revenue": money(a["revenue"]),
                                          "samples": a["samples"],
                                          "days_left": sum(1 for d in days if d["weekday"] == w)}
                                         for w, a in weekday_averages(month_data["daily"], as_of).items()]}}


def branch_budget_rows(conn, intercars) -> list[dict]:
    """Biudžetas vs faktas vs prognozė kiekvienam filialui."""
    rows = []
    for branch in ("vln", "kns"):
        f = forecast_block(conn, branch, intercars.fetch("month", branch))
        rows.append({"branch": branch, "label": per.BRANCH_LABELS[branch], "budget_revenue": f["budget_revenue"],
                     "actual_revenue": f["revenue_to_date"], "forecast_revenue": f["forecast_revenue"],
                     "budget_gross_profit": f["budget_gross_profit"], "actual_gross_profit": f["gross_profit_to_date"],
                     "forecast_gross_profit": f["forecast_gross_profit"], "vs_budget_pct": f["vs_budget_pct"],
                     "progress_pct": f["progress_pct"]})
    return rows


def kpi_block(sales: dict, costs: dict) -> dict:
    cur, prev = sales["current"], sales["previous"]
    cost_now = sum(g["amount"] for g in costs["groups"].values())
    cost_prev = sum(g["amount"] for g in costs["previous_groups"].values())
    gross = kpi(cur["gross_profit"], prev["gross_profit"])
    gross["margin_pct"] = per.ratio_pct(cur["gross_profit"], cur["revenue"])
    gross["prev_margin_pct"] = per.ratio_pct(prev["gross_profit"], prev["revenue"])
    return {"sales": kpi(cur["revenue"], prev["revenue"]), "gross_profit": gross, "costs": kpi(cost_now, cost_prev),
            "net_result": kpi(cur["gross_profit"] - cost_now, prev["gross_profit"] - cost_prev),
            "orders": {"value": cur["orders"], "prev": prev["orders"], "change_pct": per.pct_change(cur["orders"], prev["orders"])}}


KPI_LABELS = [("sales", "Pardavimai", ["intercars"]), ("gross_profit", "Bendrasis pelnas", ["intercars"]),
              ("costs", "Kaštai", ["rivile"]), ("net_result", "Grynasis rezultatas", ["intercars", "rivile"])]


def kpi_list(conn, block: dict) -> list[dict]:
    """KPI masyvas UI: kiekviena kortelė su savo šaltiniu, as_of ir būsena."""
    out = []
    for key, label, sources in KPI_LABELS:
        k = block[key]
        out.append({"key": "net" if key == "net_result" else key, "label": label, "value": k["value"], "prev": k["prev"],
                    "delta_pct": k["change_pct"], **({"margin_pct": k["margin_pct"]} if "margin_pct" in k else {}),
                    **connectors.envelope(conn, sources)})
    return out


def summary(conn, period: str, branch: str) -> dict:
    per.validate(period, branch)
    c = connectors.demo_connectors(conn)
    sales, costs = c["intercars"].fetch(period, branch), c["rivile"].fetch(period, branch)
    month = sales if period == "month" else c["intercars"].fetch("month", branch)
    fc = forecast_block(conn, branch, month)
    block = kpi_block(sales, costs)
    return {
        "period": period, "branch": branch, "range": sales["range"].as_dict(),
        "previous_range": sales["previous_range"].as_dict(), "kpis": kpi_list(conn, block), "orders": block["orders"],
        "forecast": {k: v for k, v in fc.items() if k != "days"},
        "budget_by_branch": branch_budget_rows(conn, c["intercars"]),
        "costs_note": "Kaštai pagal dokumentų datas: trumpuose laikotarpiuose sumos nelygios (pvz. DU mokamas du kartus per mėnesį).",
        "actions": actions.build(conn, branch, fc),
        **connectors.envelope(conn, ["intercars", "rivile", "personalas", "telefonija", "pastas"]),
    }


def _point(day: date, row: dict | None) -> dict:
    closed = row is None
    reason = None
    if closed:
        reason = workdays.holiday_name(day) or "Sekmadienis"
    revenue, gross = (row["revenue"], row["gross_profit"]) if row else (0.0, 0.0)
    return {"date": day.isoformat(), "weekday": day.weekday(), "sales": money(revenue), "revenue": money(revenue),
            "gross_profit": money(gross), "kind": "fact",
            "orders": row["orders"] if row else 0, "margin_pct": per.ratio_pct(gross, revenue) if row else None,
            "closed": closed, "closed_reason": reason, "forecast": False}


def sales_series(conn, period: str, branch: str) -> dict:
    """Faktai pagal pasirinktą laikotarpį; mėnesio prognozė tik kai laikotarpis siekia vakar (tęsiasi į einamą mėnesį)."""
    per.validate(period, branch)
    inter = connectors.demo_connectors(conn)["intercars"]
    data = inter.fetch(period, branch)
    rng, as_of = data["range"], data["as_of"]
    by_day = {r["day"]: r for r in data["daily"]}
    points = [_point(d, by_day.get(d)) for d in workdays.days_between(rng.start, rng.end)]
    current_month = per.month_start(as_of + timedelta(days=1))
    # Diena = vakar; jei vakar buvo praėjęs mėnuo (pvz. gruodžio 1 d.), einamojo mėnesio prognozė nerodoma.
    with_forecast = rng.end == as_of and (period != "day" or rng.start >= current_month)
    out = {"period": period, "branch": branch, "range": {"from": rng.start.isoformat(), "to": rng.end.isoformat()},
           "points": points, "forecast_month": None, "how": None,
           "totals": {"revenue": money(sum(p["revenue"] for p in points)),
                      "gross_profit": money(sum(p["gross_profit"] for p in points))},
           **connectors.envelope(conn, ["intercars"])}
    if with_forecast:
        month = data if period == "month" else inter.fetch("month", branch)
        fc = forecast_block(conn, branch, month)
        out["points"] = points + [{**d, "sales": d["revenue"], "kind": "forecast", "forecast": True} for d in fc["days"]]
        out["forecast_month"] = {k: v for k, v in fc.items() if k not in ("days", "how")}
        out["how"] = fc["how"]
    return out
