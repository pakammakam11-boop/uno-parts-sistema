"""Efektyvumas: pelnas vienam FTE, vienai eilutei, per valandą; gamintojai, pardavėjai, filialai."""
from __future__ import annotations

import random
from datetime import date

from core.dashboard import connectors, queries
from core.dashboard import generator as gen
from core.dashboard import periods as per
from core.dashboard.metrics import money

_jitter_cache: dict[tuple[str, int, int], float] = {}


def fte_for(branch: str) -> float:
    return round(sum(gen.FTE.values()), 1) if branch == "all" else gen.FTE[branch]


def headcount_for(branch: str) -> int:
    return sum(gen.HEADCOUNT.values()) if branch == "all" else gen.HEADCOUNT[branch]


def worked_hours(rng: per.Range, fte: float) -> float:
    """Darbo valandos laikotarpyje: FTE x dienos valandos (šeštadienį rotacinė pamaina, sekmadienį 0)."""
    return sum(gen.HOURS_PER_DAY.get(d.weekday(), 0.0) * fte for d in rng.days() if per.is_trading_day(d))


def _weight(name: str, share: float, day: date) -> float:
    iso = day.isocalendar()
    key = (name, iso.year, iso.week)
    if key not in _jitter_cache:
        _jitter_cache[key] = random.Random(f"{gen.SEED}|w|{name}|{iso.year}-{iso.week}").uniform(0.85, 1.15)
    return share * _jitter_cache[key]


def seller_ranking(inter, period: str, branches: list[str]) -> list[dict]:
    """Pardavėjų pelnas = filialo bendrasis pelnas dalijamas pagal kintamus svorius; valandos pagal darbo dienas."""
    out = []
    for branch in branches:
        data = inter.fetch(period, branch)
        rng = data["range"]
        sellers = [s for s in gen.SELLERS if s[1] == branch]
        profit = {s[0]: 0.0 for s in sellers}
        sales = {s[0]: 0.0 for s in sellers}
        for row in (r for r in data["daily"] if rng.start <= r["day"] <= rng.end):
            weights = {s[0]: _weight(s[0], s[2], row["day"]) for s in sellers}
            total_w = sum(weights.values())
            for name, w in weights.items():
                profit[name] += row["gross_profit"] * w / total_w
                sales[name] += row["revenue"] * w / total_w
        for name, _, _, hours_coef in sellers:
            hours = worked_hours(rng, 1.0) * hours_coef
            out.append({"name": name, "branch": branch, "branch_label": per.BRANCH_LABELS[branch],
                        "gross_profit": money(profit[name]), "profit": money(profit[name]), "sales": money(sales[name]),
                        "hours": round(hours, 1),
                        "profit_per_hour": money(profit[name] / hours) if hours else None})
    out.sort(key=lambda s: -s["gross_profit"])
    return [{"rank": i, **s} for i, s in enumerate(out, 1)]


def _sales_line(key: str, r: dict, total_revenue: float) -> dict:
    return {"key": key, "revenue": money(r["revenue"]), "gross_profit": money(r["gross_profit"]),
            "margin_pct": per.ratio_pct(r["gross_profit"], r["revenue"]), "lines": r["lines"], "orders": r["orders"],
            "profit_per_line": money(r["gross_profit"] / r["lines"]) if r["lines"] else None,
            "share_pct": per.ratio_pct(r["revenue"], total_revenue)}


def manufacturers(data: dict) -> list[dict]:
    total = data["current"]["revenue"]
    rows = [{"manufacturer": r["key"], "name": r["key"], **_sales_line(r["key"], r, total)} for r in data["by_manufacturer"]]
    for row in rows:
        row["sales"], row["profit"] = row["revenue"], row["gross_profit"]
    for row in rows:
        del row["key"]
    return sorted(rows, key=lambda r: -r["gross_profit"])


def branch_comparison(conn, data: dict, rng: per.Range) -> list[dict]:
    total = sum(r["revenue"] for r in data["by_branch"])
    rows = []
    for r in data["by_branch"]:
        b = r["key"]
        fte, hours = gen.FTE[b], worked_hours(rng, gen.FTE[b])
        budget = queries.budget(conn, f"{rng.end.year}-{rng.end.month:02d}", b)
        rows.append({"branch": b, "key": b, "sales": money(r["revenue"]), "profit": money(r["gross_profit"]),
                     "budget_sales": money(budget["revenue"]), "budget_profit": money(budget["gross_profit"]),
                     "label": per.BRANCH_LABELS[b], "fte": fte, "headcount": gen.HEADCOUNT[b],
                     **{k: v for k, v in _sales_line(b, r, total).items() if k != "key"},
                     "returns_pct": per.ratio_pct(r["returns_amount"], r["revenue"]),
                     "revenue_per_fte": money(r["revenue"] / fte), "profit_per_fte": money(r["gross_profit"] / fte),
                     "profit_per_hour": money(r["gross_profit"] / hours) if hours else None})
    return sorted(rows, key=lambda r: r["branch"])


def efficiency(conn, period: str, branch: str) -> dict:
    per.validate(period, branch)
    inter = connectors.demo_connectors(conn)["intercars"]
    data = inter.fetch(period, branch)
    rng, cur = data["range"], data["current"]
    fte = fte_for(branch)
    hours = worked_hours(rng, fte)
    branches = ["vln", "kns"] if branch == "all" else [branch]
    result = {
        "period": period, "branch": branch, "range": rng.as_dict(),
        "fte": fte, "headcount": headcount_for(branch), "hours": round(hours, 1), "lines": cur["lines"],
        "per_fte": money(cur["gross_profit"] / fte), "profit_per_fte": money(cur["gross_profit"] / fte),
        "profit_per_line": money(cur["gross_profit"] / cur["lines"]) if cur["lines"] else None,
        "profit_per_hour": money(cur["gross_profit"] / hours) if hours else None,
        "kpis": {"revenue": money(cur["revenue"]), "gross_profit": money(cur["gross_profit"]), "lines": cur["lines"],
                 "margin_pct": per.ratio_pct(cur["gross_profit"], cur["revenue"]),
                 "profit_per_fte": money(cur["gross_profit"] / fte), "revenue_per_fte": money(cur["revenue"] / fte),
                 "profit_per_line": money(cur["gross_profit"] / cur["lines"]) if cur["lines"] else None,
                 "profit_per_hour": money(cur["gross_profit"] / hours) if hours else None},
        "manufacturers": manufacturers(data), "staff": seller_ranking(inter, period, branches),
        "branches": branch_comparison(conn, data, rng),
        "notes": ["Pelnas = bendrasis pelnas (pardavimai - savikaina), prieš kaštus.",
                  "Santykiai skaičiuojami iš sumų (suma / suma), ne iš dienų ar eilučių vidurkių.",
                  "FTE 54,5 (57 darbuotojų); darbo valandos = FTE x dienos valandos, šeštadienį rotacinė pamaina.",
                  "Pardavėjų pelnas paskirstomas pagal filialo bendrąjį pelną (demo modelis, ne tikros sąskaitos)."],
        **connectors.envelope(conn, ["intercars"]),
    }
    return {**result, "employees": result["staff"]}
