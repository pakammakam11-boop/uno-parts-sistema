"""Dashboard faktų lentelės (prefiksas dash_), toje pačioje DB kaip HR (core.hr.db variklis)."""
from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Float, Index, Integer, MetaData, String, Table, Text, UniqueConstraint

metadata = MetaData()

dash_meta = Table("dash_meta", metadata,
                  Column("key", String(64), primary_key=True), Column("value", Text, nullable=False))

dash_sales_daily = Table(
    "dash_sales_daily", metadata,
    Column("day", Date, primary_key=True), Column("branch", String(8), primary_key=True),
    Column("manufacturer", String(40), primary_key=True),
    Column("orders", Integer, nullable=False), Column("lines", Integer, nullable=False),
    Column("revenue", Float, nullable=False), Column("cogs", Float, nullable=False),
    Column("returns_count", Integer, nullable=False), Column("returns_amount", Float, nullable=False),
)

dash_cost_lines = Table(
    "dash_cost_lines", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("day", Date, nullable=False), Column("branch", String(8), nullable=False),
    Column("grp", String(16), nullable=False), Column("supplier", String(120), nullable=False),
    Column("doc_no", String(32), nullable=False), Column("line", Integer, nullable=False, default=1, server_default="1"),
    Column("amount", Float, nullable=False), Column("source", String(24), nullable=False),
    UniqueConstraint("doc_no", "line", name="uq_dash_cost_doc_line"),
)
Index("ix_dash_cost_day", dash_cost_lines.c.day)

dash_ops_daily = Table(
    "dash_ops_daily", metadata,
    Column("day", Date, primary_key=True), Column("branch", String(8), primary_key=True),
    Column("calls_total", Integer, nullable=False), Column("missed_calls", Integer, nullable=False),
    Column("stops", Integer, nullable=False), Column("km", Float, nullable=False),
    Column("late_stops", Integer, nullable=False),
)

dash_deposits = Table(
    "dash_deposits", metadata,
    Column("id", Integer, primary_key=True, autoincrement=False), Column("branch", String(8), nullable=False),
    Column("supplier", String(60), nullable=False), Column("qty", Integer, nullable=False),
    Column("value", Float, nullable=False), Column("created_day", Date, nullable=False),
    Column("status", String(12), nullable=False),
)

dash_work_items = Table(
    "dash_work_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(8), nullable=False),               # mail | task
    Column("priority", String(8), nullable=False),           # high | normal | low
    Column("title", String(200), nullable=False), Column("counterparty", String(120), nullable=False),
    Column("category", String(24), nullable=False), Column("mailbox", String(24), nullable=False),
    Column("received_at", DateTime), Column("due_day", Date),
)

dash_budget = Table(
    "dash_budget", metadata,
    Column("month", String(7), primary_key=True), Column("branch", String(8), primary_key=True),
    Column("revenue", Float, nullable=False), Column("gross_profit", Float, nullable=False),
)
