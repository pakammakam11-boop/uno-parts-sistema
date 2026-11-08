"""Demo faktų pasėjimas: vieną kartą per versiją, vėliau kasdien papildoma naujomis dienomis.

Greitis: viskas įrašoma masiniu insert (apie 9 000 eilučių, < 1 s SQLite).
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from sqlalchemy import delete, insert, inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite

from core.dashboard import generator as gen
from core.dashboard import periods
from core.dashboard.schema import (dash_budget, dash_cost_lines, dash_deposits, dash_meta, dash_ops_daily,
                                   dash_sales_daily, dash_work_items, metadata)
from core.hr import clock, db, workdays

DATA_VERSION = "2"           # 2: dash_cost_lines.line + UNIQUE(doc_no, line); senos lentelės perkuriamos
HISTORY_MONTHS = 15
SCENARIO_DAYS_BACK = 27        # kuro kainų šuolis prasidėjo prieš ~4 sav. pirmo pasėjimo metu
OPS_REFRESH_DAYS = 7           # paskutinių dienų operacijos perskaičiuojamos (skambučių scenarijus)
ADVISORY_LOCK_KEY = 7_410_220_611      # pg_advisory_xact_lock: schemos paruošimas + sync vienu metu tik vienoje instancijoje
_ready: set[tuple[str, date]] = set()
_lock = threading.Lock()


def _meta_get(conn, key: str) -> str | None:
    return conn.execute(select(dash_meta.c.value).where(dash_meta.c.key == key)).scalar()


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(delete(dash_meta).where(dash_meta.c.key == key))
    conn.execute(insert(dash_meta).values(key=key, value=value))


def _bulk(conn, table, rows: list[dict]) -> None:
    """Masinis insert; pasikartojantys raktai (pvz. kaštų dokumentas) tyliai praleidžiami, todėl sync idempotentiškas."""
    if not rows:
        return
    dialect = {"postgresql": postgresql, "sqlite": sqlite}.get(conn.dialect.name)
    stmt = dialect.insert(table).on_conflict_do_nothing() if dialect else insert(table)
    conn.execute(stmt, rows)


def _serialize(conn) -> None:
    """Postgres: advisory užraktas iki transakcijos pabaigos. SQLite: BEGIN IMMEDIATE jau paimtas db.tx()."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})


def _wipe(conn) -> None:
    for table in (dash_sales_daily, dash_cost_lines, dash_ops_daily, dash_deposits, dash_work_items, dash_budget,
                  dash_meta):
        conn.execute(delete(table))


def history_start(as_of: date) -> date:
    return periods.month_start(periods.add_months(as_of, -(HISTORY_MONTHS - 1)))


def _sales_and_costs(conn, first: date, as_of: date, anchor: date) -> None:
    sales = [row for day in workdays.days_between(first, as_of) for row in gen.sales_rows(day)]
    _bulk(conn, dash_sales_daily, sales)
    month = periods.month_start(first)
    costs: list[dict] = []
    while month <= as_of:
        costs += [r for r in gen.cost_lines(month.year, month.month, anchor) if first <= r["day"] <= as_of]
        month = periods.add_months(month, 1)
    _bulk(conn, dash_cost_lines, costs)


def _ops(conn, first: date, as_of: date) -> None:
    spike = periods.last_trading_day(as_of)
    rows = []
    for day in workdays.days_between(first, as_of):
        rows += gen.ops_rows(day, gen.sales_rows(day), spike)
    _bulk(conn, dash_ops_daily, rows)


def _budgets(conn, first: date, today: date) -> None:
    """Biudžetai nuo istorijos pradžios iki einamojo mėnesio imtinai (mėnesio pirmą dieną faktų dar nėra, bet prognozei reikia)."""
    have = {r[0] for r in conn.execute(select(dash_budget.c.month))}
    month = periods.month_start(first)
    rows = []
    while month <= periods.month_start(today):
        if f"{month.year}-{month.month:02d}" not in have:
            rows += gen.budget_rows(month.year, month.month)
        month = periods.add_months(month, 1)
    _bulk(conn, dash_budget, rows)


def _daily_items(conn, today: date) -> None:
    """Depozitai ir Liutauro darbai santykiniai pagal šiandien: perrašomi vieną kartą per dieną."""
    if _meta_get(conn, "work_day") == today.isoformat():
        return
    conn.execute(delete(dash_deposits))
    conn.execute(delete(dash_work_items))
    now = datetime.fromtimestamp(clock.now(), clock.TZ).replace(tzinfo=None)
    _bulk(conn, dash_deposits, gen.deposits(today))
    _bulk(conn, dash_work_items, gen.work_items(now))
    _meta_set(conn, "work_day", today.isoformat())


def sync(conn, today: date) -> None:
    """Paverčia DB tokią, kad faktai siektų vakar. Idempotentiška."""
    as_of = periods.as_of_day(today)
    if _meta_get(conn, "version") != DATA_VERSION:
        _wipe(conn)
        _meta_set(conn, "version", DATA_VERSION)
        _meta_set(conn, "scenario_anchor", (today - timedelta(days=SCENARIO_DAYS_BACK)).isoformat())
    anchor = date.fromisoformat(_meta_get(conn, "scenario_anchor"))
    through = _meta_get(conn, "data_through")
    first = date.fromisoformat(through) + timedelta(days=1) if through else history_start(as_of)
    if first <= as_of:
        _sales_and_costs(conn, first, as_of, anchor)
        ops_from = min(first, as_of - timedelta(days=OPS_REFRESH_DAYS))
        conn.execute(delete(dash_ops_daily).where(dash_ops_daily.c.day >= ops_from))
        _ops(conn, ops_from, as_of)
        _meta_set(conn, "data_through", as_of.isoformat())
    _budgets(conn, history_start(as_of), today)
    _daily_items(conn, today)


def prepare(today: date) -> None:
    """Viena transakcija: užraktas -> schema -> meta skaitoma tik po užrakto -> sync. Saugu kelioms instancijoms."""
    with db.tx() as conn:
        _serialize(conn)
        _rebuild_if_old_schema(conn)
        metadata.create_all(conn)
        sync(conn, today)


def _rebuild_if_old_schema(conn) -> None:
    """Demo faktai pergeneruojami, todėl pasikeitus versijai dash_ lentelės tiesiog perkuriamos (create_all neprideda stulpelių)."""
    if not inspect(conn).has_table(dash_meta.name):
        return
    if _meta_get(conn, "version") != DATA_VERSION:
        metadata.drop_all(conn)


def ready():
    """Variklis su dash_ lentelėmis ir faktais iki vakar (vieną kartą per procesą, DB ir dieną)."""
    engine = db.get_engine()
    today = clock.today()
    key = (str(engine.url), today)
    with _lock:
        if key in _ready:
            return engine
        prepare(today)
        _ready.add(key)
    return engine
