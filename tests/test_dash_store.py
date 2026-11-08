"""Faktų lentelių pasėjimas: vieną kartą, greitai, papildymas kasdien, 15 mėnesių istorija."""
import time
from datetime import date

from sqlalchemy import func, select

from core.dashboard import store
from core.dashboard.schema import dash_budget, dash_cost_lines, dash_meta, dash_ops_daily, dash_sales_daily, dash_work_items
from core.hr import db
from dash_testkit import freeze
from hr_testkit import hr_env  # noqa: F401


def count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar()


def totals():
    with db.tx() as conn:
        return (conn.execute(select(func.sum(dash_sales_daily.c.revenue))).scalar(),
                conn.execute(select(func.sum(dash_cost_lines.c.amount))).scalar())


def test_seed_once_is_fast_and_covers_15_months(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    started = time.time()
    store.ready()
    assert time.time() - started < 5
    with db.tx() as conn:
        first, last = conn.execute(select(func.min(dash_sales_daily.c.day), func.max(dash_sales_daily.c.day))).one()
        assert first == date(2025, 9, 1) and last == date(2026, 11, 6)
        assert count(conn, dash_cost_lines) > 800 and count(conn, dash_budget) == 2 * 15
        assert count(conn, dash_work_items) == 16
        assert conn.execute(select(func.max(dash_cost_lines.c.day))).scalar() <= date(2026, 11, 6)


def test_ready_is_idempotent(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    store._ready.clear()
    store.ready()
    with db.tx() as conn:
        sales = count(conn, dash_sales_daily)
    store._ready.clear()
    store.ready()
    with db.tx() as conn:
        assert count(conn, dash_sales_daily) == sales


def test_new_day_appends_without_duplicates(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    with db.tx() as conn:
        before = count(conn, dash_sales_daily)
        scenario = conn.execute(select(dash_meta.c.value).where(dash_meta.c.key == "scenario_anchor")).scalar()
    freeze(monkeypatch, date(2026, 11, 10))      # vakar = pirmadienis; 11-07..11-09 jau nebuvo pasėta
    store.ready()
    with db.tx() as conn:
        added = count(conn, dash_sales_daily) - before
        assert added == 2 * 18          # 11-07 (šeštadienis) ir 11-09 (pirmadienis); 11-08 sekmadienis tuščias
        assert conn.execute(select(func.max(dash_sales_daily.c.day))).scalar() == date(2026, 11, 9)
        assert conn.execute(select(dash_meta.c.value).where(dash_meta.c.key == "scenario_anchor")).scalar() == scenario


def test_same_clock_gives_identical_data_in_two_databases(hr_env, monkeypatch, tmp_path):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    first = totals()
    monkeypatch.setenv("HR_DATABASE_URL", f"sqlite:///{tmp_path / 'second.db'}")
    store.ready()
    assert totals() == first


def test_recent_ops_have_missed_call_scenario(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    with db.tx() as conn:
        missed = conn.execute(select(func.sum(dash_ops_daily.c.missed_calls))
                              .where(dash_ops_daily.c.day == date(2026, 11, 6))).scalar()
    assert missed == 23


def test_duplicate_cost_documents_are_ignored(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    with db.tx() as conn:
        before = count(conn, dash_cost_lines)
        rows = [dict(r) for r in conn.execute(select(dash_cost_lines).limit(5)).mappings()]
        for r in rows:
            r.pop("id")
        store._bulk(conn, dash_cost_lines, rows)
        assert count(conn, dash_cost_lines) == before


def test_concurrent_syncs_do_not_double_costs(hr_env, monkeypatch):
    import threading
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    freeze(monkeypatch, date(2026, 11, 16))          # nauja diena: abu "procesai" mato seną data_through
    barrier, errors = threading.Barrier(2), []

    def worker():
        try:
            barrier.wait(timeout=10)
            store.prepare(date(2026, 11, 16))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert not errors, errors
    parallel = totals()
    store._ready.clear()
    other = db.get_engine().url.database + ".single"
    monkeypatch.setenv("HR_DATABASE_URL", f"sqlite:///{other}")
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()
    freeze(monkeypatch, date(2026, 11, 16))
    store.prepare(date(2026, 11, 16))
    assert totals() == parallel


def test_launchers_enable_demo_login():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    for name in ("paleisti.bat", "paleisti_app.bat"):
        assert "set HR_DEMO=1" in (root / name).read_text(encoding="utf-8"), name
