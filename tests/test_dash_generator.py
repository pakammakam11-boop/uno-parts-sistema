"""Demo generatoriaus savybės: determinizmas, sezoniškumas, apimtys, marža, grąžinimai."""
from datetime import date

from core.dashboard import generator as gen


def month_rows(year, month, days=31):
    rows = []
    for d in range(1, days + 1):
        try:
            rows += gen.sales_rows(date(year, month, d))
        except ValueError:
            break
    return rows


def test_sales_rows_are_deterministic():
    assert gen.sales_rows(date(2026, 9, 10)) == gen.sales_rows(date(2026, 9, 10))
    assert gen.sales_rows(date(2026, 9, 10)) != gen.sales_rows(date(2026, 9, 11))


def test_cost_lines_and_budget_are_deterministic():
    assert gen.cost_lines(2026, 9) == gen.cost_lines(2026, 9)
    assert gen.budget_rows(2026, 9) == gen.budget_rows(2026, 9)
    assert gen.deposits(date(2026, 11, 7)) == gen.deposits(date(2026, 11, 7))


def test_sunday_and_holidays_have_no_sales():
    assert gen.sales_rows(date(2026, 11, 8)) == []            # sekmadienis
    assert gen.sales_rows(date(2026, 12, 25)) == []           # Kalėdos
    assert gen.sales_rows(date(2026, 11, 7)) != []            # šeštadienis dirba


def test_saturday_is_low():
    friday = sum(r["revenue"] for r in gen.sales_rows(date(2026, 9, 11)))
    saturday = sum(r["revenue"] for r in gen.sales_rows(date(2026, 9, 12)))
    assert saturday < friday * 0.6


def test_month_has_about_five_thousand_orders():
    totals = [sum(r["orders"] for r in month_rows(2026, month, 30)) for month in range(1, 13)]
    assert 4500 <= sum(totals) / 12 <= 5500
    assert all(4000 <= t <= 6200 for t in totals)
    assert totals[3] > totals[7] and totals[9] > totals[7]          # pavasaris ir ruduo didesni už vasarą


def test_gross_margin_about_27_percent_and_returns_about_10():
    rows = month_rows(2026, 9, 30)
    revenue = sum(r["revenue"] for r in rows)
    gross = revenue - sum(r["cogs"] for r in rows)
    assert 0.26 <= gross / revenue <= 0.28
    assert 0.09 <= sum(r["returns_amount"] for r in rows) / revenue <= 0.11


def test_manufacturers_have_different_margins():
    rows = month_rows(2026, 9, 30)
    margins = {}
    for r in rows:
        rev, cogs = margins.get(r["manufacturer"], (0, 0))
        margins[r["manufacturer"]] = (rev + r["revenue"], cogs + r["cogs"])
    pct = {m: (rev - cogs) / rev for m, (rev, cogs) in margins.items()}
    assert {"Bosch", "Febi", "Mann", "TRW", "Sachs", "Valeo", "NK", "Kamoka"} <= set(pct)
    assert max(pct.values()) - min(pct.values()) > 0.05


def test_cost_lines_cover_all_groups_with_documents():
    lines = gen.cost_lines(2026, 9)
    assert {l["grp"] for l in lines} == set(gen.COST_GROUPS)
    assert all(l["doc_no"] and l["supplier"] and l["amount"] > 0 for l in lines)
    assert len({l["doc_no"] for l in lines}) == len(lines)


def test_fuel_spike_applies_only_after_anchor():
    plain = [l for l in gen.cost_lines(2026, 10) if l["source"] == "Bankas"]
    spiked = [l for l in gen.cost_lines(2026, 10, date(2026, 10, 15)) if l["source"] == "Bankas"]
    for a, b in zip(plain, spiked):
        assert (b["amount"] / a["amount"] > 1.1) == (a["day"] >= date(2026, 10, 15))


def test_ops_spike_day_has_23_missed_calls():
    day = date(2026, 11, 6)
    rows = gen.ops_rows(day, gen.sales_rows(day), spike_day=day)
    assert sum(r["missed_calls"] for r in rows) == 23
    normal = gen.ops_rows(day, gen.sales_rows(day))
    assert sum(r["missed_calls"] for r in normal) < 15


def test_fte_matches_spec():
    assert round(sum(gen.FTE.values()), 1) == 54.5 and sum(gen.HEADCOUNT.values()) == 57
