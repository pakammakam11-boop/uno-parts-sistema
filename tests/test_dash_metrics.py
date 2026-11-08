"""Prognozė (su šventėmis), laikotarpiai, santykiai iš sumų, filialų filtras."""
from datetime import date, timedelta

import pytest

from core.dashboard import connectors, costs, efficiency, metrics, store
from core.dashboard import periods as per
from core.hr import db
from core.hr.errors import HrError
from dash_testkit import freeze
from hr_testkit import hr_env  # noqa: F401


def by_key(kpis: list[dict]) -> dict:
    return {k["key"]: k for k in kpis}


def synthetic_daily(end: date, days: int, revenue_by_weekday: dict[int, float]) -> list[dict]:
    rows = []
    for k in range(days - 1, -1, -1):
        d = end - timedelta(days=k)
        if per.is_trading_day(d):
            rev = revenue_by_weekday[d.weekday()]
            rows.append({"day": d, "revenue": rev, "gross_profit": rev * 0.25, "orders": 10, "cogs": rev * 0.75})
    return rows


# ---------- laikotarpiai ----------

def test_period_ranges():
    as_of = date(2026, 11, 18)          # trečiadienis
    assert (per.resolve("day", as_of).start, per.resolve("day", as_of).end) == (as_of, as_of)
    assert per.resolve("week", as_of).start == date(2026, 11, 16)
    assert per.resolve("month", as_of).start == date(2026, 11, 1)
    assert per.resolve("quarter", as_of).start == date(2026, 10, 1)


def test_day_period_skips_sunday_and_holiday():
    assert per.resolve("day", date(2026, 11, 8)).end == date(2026, 11, 7)        # sekmadienis -> šeštadienis
    assert per.resolve("day", date(2026, 12, 26)).end == date(2026, 12, 23)      # Kalėdos ir šeštadienis -> trečiadienis


def test_previous_ranges_same_length():
    cur = per.resolve("month", date(2026, 3, 10))
    prev = per.previous("month", cur)
    assert (prev.start, prev.end) == (date(2026, 2, 1), date(2026, 2, 10))
    wk = per.previous("week", per.resolve("week", date(2026, 11, 18)))
    assert (wk.start, wk.end) == (date(2026, 11, 9), date(2026, 11, 11))


def test_pct_helpers():
    assert per.pct_change(110, 100) == 10.0 and per.pct_change(5, 0) is None and per.ratio_pct(1, 0) is None


# ---------- prognozė ----------

def test_forecast_days_skip_sundays_and_holidays():
    as_of = date(2026, 12, 20)           # sekmadienis, likusios 12-21..12-31
    avgs = {w: {"revenue": 100.0, "gross_profit": 25.0} for w in range(6)}
    days = metrics.forecast_days(avgs, as_of)
    assert [d["date"] for d in days] == [f"2026-12-{n}" for n in (21, 22, 23, 28, 29, 30, 31)]


def test_forecast_math_is_actual_plus_weekday_averages():
    as_of = date(2026, 12, 18)           # penktadienis
    by_weekday = {0: 1000.0, 1: 900.0, 2: 800.0, 3: 700.0, 4: 600.0, 5: 200.0}
    daily = synthetic_daily(as_of, 63, by_weekday)
    avgs = metrics.weekday_averages(daily, as_of)
    assert avgs[0]["revenue"] == 1000.0 and avgs[5]["revenue"] == 200.0
    days = metrics.forecast_days(avgs, as_of)
    # likę: 12-19 (š), 21-23 (pirm.-treč.), 24-26 šventės, 28 (pirm.), 29 (antr.), 30 (treč.), 31 (ketv.)
    assert sum(d["revenue"] for d in days) == 200 + 1000 + 900 + 800 + 1000 + 900 + 800 + 700
    assert days[0]["date"] == "2026-12-19"


def test_holiday_row_is_excluded_from_weekday_average():
    as_of = date(2026, 12, 23)
    daily = synthetic_daily(as_of, 63, {w: 500.0 for w in range(6)})
    daily.append({"day": date(2026, 12, 25), "revenue": 99999.0, "gross_profit": 1.0, "orders": 1, "cogs": 1.0})
    assert metrics.weekday_averages(daily, as_of)[4]["revenue"] == 500.0   # Kalėdos (penktadienis) ignoruojamos


def test_weekday_average_uses_last_four_same_weekdays_skipping_holidays():
    as_of = date(2026, 11, 6)
    daily = synthetic_daily(as_of, 63, {w: 100.0 for w in range(6)})
    for r in daily:
        if r["day"].weekday() == 0 and r["day"] >= date(2026, 10, 5):
            r["revenue"] = 400.0
    avg = metrics.weekday_averages(daily, as_of)[0]
    assert avg["revenue"] == 400.0 and avg["samples"] == 4


def test_ratio_from_sums_not_mean_of_ratios():
    total = {"revenue": 1000.0, "gross_profit": 140.0, "orders": 2, "lines": 2}   # 50 % ir 10 % dienos
    kpis = metrics.kpi_block({"current": total, "previous": total}, {"groups": {}, "previous_groups": {}})
    assert kpis["gross_profit"]["margin_pct"] == 14.0        # 140 / 1000, o ne (50 + 10) / 2 = 30


# ---------- su duomenų baze ----------

@pytest.fixture
def seeded(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()


def test_branch_filter_adds_up(seeded):
    with db.tx() as conn:
        everything = by_key(metrics.summary(conn, "month", "all")["kpis"])
        vln = by_key(metrics.summary(conn, "month", "vln")["kpis"])
        kns = by_key(metrics.summary(conn, "month", "kns")["kpis"])
    for key in ("sales", "gross_profit", "costs"):
        assert abs(everything[key]["value"] - vln[key]["value"] - kns[key]["value"]) < 0.05
    assert vln["sales"]["value"] > kns["sales"]["value"] > 0


def test_summary_margin_is_gross_profit_over_sales(seeded):
    with db.tx() as conn:
        k = by_key(metrics.summary(conn, "quarter", "all")["kpis"])
    assert abs(k["gross_profit"]["margin_pct"] - k["gross_profit"]["value"] / k["sales"]["value"] * 100) < 0.06
    assert abs(k["net"]["value"] - (k["gross_profit"]["value"] - k["costs"]["value"])) < 0.02


def test_forecast_block_in_summary(seeded):
    with db.tx() as conn:
        f = metrics.summary(conn, "week", "all")["forecast"]
    assert f["month"] == "2026-11" and f["remaining_trading_days"] == 20
    assert f["forecast_revenue"] > f["revenue_to_date"] > 0
    assert abs(f["vs_budget_pct"] - (f["forecast_revenue"] / f["budget_revenue"] - 1) * 100) < 0.06
    assert "faktas iki vakar" in f["how"]["text"]


def test_series_has_closed_sunday_and_forecast(seeded):
    with db.tx() as conn:
        s = metrics.sales_series(conn, "month", "all")
    facts = [p for p in s["points"] if p["kind"] == "fact"]
    forecast = [p for p in s["points"] if p["kind"] == "forecast"]
    assert facts[0]["date"] == "2026-11-01" and facts[-1]["date"] == "2026-11-06"
    sunday = facts[0]
    assert sunday["closed"] and sunday["sales"] == 0
    assert forecast and forecast[0]["date"] == "2026-11-07" and forecast[-1]["date"] == "2026-11-30"
    assert abs(sum(p["sales"] for p in facts) - s["totals"]["revenue"]) < 0.05


def test_costs_drill_down_adds_up(seeded):
    with db.tx() as conn:
        groups = costs.groups(conn, "quarter", "all")
        group = groups["groups"][0]
        suppliers = costs.suppliers(conn, group["id"], "quarter", "all")
        supplier = suppliers["suppliers"][0]
        docs = costs.documents(conn, group["id"], supplier["key"], "quarter", "all")
    assert abs(sum(g["amount"] for g in groups["groups"]) - groups["total"]) < 0.05
    assert abs(sum(s["amount"] for s in suppliers["suppliers"]) - group["amount"]) < 0.05
    assert abs(sum(d["amount"] for d in docs["documents"]) - supplier["amount"]) < 0.05
    assert len(docs["documents"]) == supplier["documents"] and docs["documents"][0]["doc_no"]


def test_efficiency_ratios_come_from_sums(seeded):
    with db.tx() as conn:
        e = efficiency.efficiency(conn, "month", "all")
    k = e["kpis"]
    assert e["fte"] == 54.5 and e["headcount"] == 57
    assert abs(k["profit_per_fte"] - k["gross_profit"] / 54.5) < 0.01
    assert abs(k["profit_per_line"] - k["gross_profit"] / k["lines"]) < 0.01
    assert abs(k["profit_per_hour"] - k["gross_profit"] / e["hours"]) < 0.01
    for m in e["manufacturers"]:
        assert abs(m["margin_pct"] - m["gross_profit"] / m["revenue"] * 100) < 0.06
    assert abs(sum(s["gross_profit"] for s in e["staff"]) - k["gross_profit"]) < 0.5
    assert [b["branch"] for b in e["branches"]] == ["kns", "vln"] and len(e["manufacturers"]) == 9
    ranking = [s["gross_profit"] for s in e["staff"]]
    assert ranking == sorted(ranking, reverse=True)


def test_every_payload_has_source_as_of_status(seeded):
    with db.tx() as conn:
        for payload in (metrics.summary(conn, "day", "all"), costs.groups(conn, "day", "all"),
                        efficiency.efficiency(conn, "day", "all"), metrics.sales_series(conn, "day", "all")):
            assert payload["source"] and payload["as_of"] and payload["status"] == "demo"
        assert connectors.envelope(conn, ["personalas"])["status"] == "connected"


def test_validation_errors_are_lithuanian(seeded):
    with db.tx() as conn:
        with pytest.raises(HrError) as e:
            metrics.summary(conn, "year", "all")
        assert e.value.status == 400 and "laikotarpis" in e.value.detail
        with pytest.raises(HrError) as e:
            metrics.summary(conn, "day", "xyz")
        assert e.value.status == 400 and "filialas" in e.value.detail


# ---------- laikotarpiai pagal šiandien (S4), pokytis (S2) ----------

def test_negative_base_gives_improvement_sign():
    assert per.pct_change(9447.56, -44170.46) == pytest.approx(121.4, abs=0.1)      # pagerėjo, o ne -121,4 %
    assert per.pct_change(-100, -50) == -100.0 and per.pct_change(5, 0) is None


def test_zero_base_kpi_has_no_comparison():
    assert metrics.kpi(10, 0)["change_pct"] is None


def test_monday_week_is_current_week_without_facts():
    as_of = date(2026, 11, 8)           # vakar sekmadienis, šiandien pirmadienis 11-09
    wk = per.resolve("week", as_of)
    assert wk.start == date(2026, 11, 9) and wk.end < wk.start and wk.days() == []
    prev = per.previous("week", wk)
    assert prev.end < prev.start        # nėra ko lyginti: tuščia, ne praėjusi savaitė


def test_first_of_month_period_is_new_month():
    as_of = date(2026, 11, 30)          # šiandien gruodžio 1 d.
    assert per.resolve("month", as_of).start == date(2026, 12, 1)
    assert per.resolve("quarter", as_of).start == date(2026, 10, 1)
    assert per.resolve("quarter", date(2026, 12, 31)).start == date(2027, 1, 1)


def test_dec_1_shows_december_forecast_and_budget(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 12, 1))
    store.ready()
    with db.tx() as conn:
        f = metrics.summary(conn, "month", "all")
        series = metrics.sales_series(conn, "month", "all")
    fc = f["forecast"]
    assert fc["month"] == "2026-12" and fc["remaining_trading_days"] > 20
    assert fc["budget_revenue"] > 0 and fc["revenue_to_date"] == 0 and fc["forecast_revenue"] > 0
    assert f["kpis"][0]["delta_pct"] is None and f["range"]["from"] == "2026-12-01"
    assert series["forecast_month"]["month"] == "2026-12" and series["points"][0]["date"] == "2026-12-01"
    assert all(p["kind"] == "forecast" for p in series["points"])
    with db.tx() as conn:               # kiti galai su tuščiu laikotarpiu nelūžta
        for period in ("day", "week", "month", "quarter"):
            efficiency.efficiency(conn, period, "all")
            costs.groups(conn, period, "all")


def test_dec_1_day_period_has_no_december_forecast(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 12, 1))
    store.ready()
    with db.tx() as conn:
        day = metrics.sales_series(conn, "day", "all")
        week = metrics.sales_series(conn, "week", "all")
    assert day["range"]["to"] == "2026-11-30" and day["forecast_month"] is None
    assert all(p["kind"] == "fact" for p in day["points"])
    assert week["forecast_month"]["month"] == "2026-12"     # savaitė prasideda gruodyje: prognozė lieka
