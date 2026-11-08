"""Lentelių aprašai (SQLAlchemy Core). Visos lentelės su prefiksu hr_; veikia ir SQLite, ir Postgres."""
from __future__ import annotations

from sqlalchemy import (Boolean, Column, Date, Float, ForeignKey, Integer, LargeBinary, MetaData, String, Table, Text,
                        UniqueConstraint)

metadata = MetaData()

hr_meta = Table("hr_meta", metadata,
                Column("key", String(64), primary_key=True), Column("value", Text, nullable=False))

hr_employees = Table(
    "hr_employees", metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("company", String(120), nullable=False),
    Column("name", String(120), nullable=False),
    Column("email", String(160), nullable=False, unique=True),
    Column("position", String(120), nullable=False),
    Column("department", String(80), nullable=False),
    Column("manager_id", Integer, nullable=False),          # direktoriaus vadovas yra jis pats
    Column("is_hr", Boolean, nullable=False, default=False),
    Column("balance", Float, nullable=False, default=0),    # kasmetinių atostogų likutis, d.
    Column("active", Boolean, nullable=False, default=True),
)

hr_sessions = Table(
    "hr_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("employee_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("csrf", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
)

hr_magic_links = Table(
    "hr_magic_links", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("employee_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("used_at", Float),
)

hr_requests = Table(
    "hr_requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(16), nullable=False),              # leave | trip
    Column("leave_kind", String(16)),                        # annual | unpaid | study
    Column("employee_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("substitute_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("date_from", Date, nullable=False),
    Column("date_to", Date, nullable=False),
    Column("work_days", Integer, nullable=False),
    Column("destination", String(200)),
    Column("purpose", Text),
    Column("comment", Text),
    Column("status", String(24), nullable=False),            # pending_signatures|approved|rejected|cancelled
    Column("order_status", String(24)),                      # pending_order|order_signed|order_rejected
    Column("reason", Text),                                  # atsisakymo priežastis
    Column("declined_by", Integer),
    Column("effects_applied", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
)

hr_documents = Table(
    "hr_documents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, ForeignKey("hr_requests.id"), nullable=False),
    Column("kind", String(16), nullable=False),              # request | order
    Column("title", String(200), nullable=False),
    Column("pdf", LargeBinary, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("payload", Text, nullable=False),                 # JSON, iš kurio perpiešiamas pasirašytas PDF
    Column("signing_token", String(80), nullable=False, unique=True),
    Column("status", String(16), nullable=False),            # pending|completed|declined|cancelled
    Column("signed_pdf", LargeBinary),
    Column("created_at", Float, nullable=False),
)

hr_signers = Table(
    "hr_signers", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, ForeignKey("hr_documents.id"), nullable=False),
    Column("signer_id", String(16), nullable=False),         # s1, s2... (Dokobit signer_id analogas)
    Column("employee_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("role", String(16), nullable=False),              # employee | substitute | manager
    Column("sequence", Integer, nullable=False),
    Column("status", String(16), nullable=False),            # pending|signed|declined
    Column("signed_at", Float),
    Column("method", String(40)),
    Column("signed_sha256", String(64)),
    Column("reason", Text),
    UniqueConstraint("document_id", "signer_id"),
)

hr_marks = Table(
    "hr_marks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("employee_id", Integer, ForeignKey("hr_employees.id"), nullable=False),
    Column("day", Date, nullable=False),
    Column("mark", String(4), nullable=False),               # A | NA | MA | K
    Column("request_id", Integer, ForeignKey("hr_requests.id"), nullable=False),
    UniqueConstraint("employee_id", "day"),
)

hr_audit = Table(
    "hr_audit", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, ForeignKey("hr_requests.id"), nullable=False),
    Column("document_id", Integer),
    Column("ts", Float, nullable=False),
    Column("actor_id", Integer),
    Column("action", String(40), nullable=False),
    Column("detail", Text, nullable=False),
)

hr_login_attempts = Table(
    "hr_login_attempts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(80), nullable=False, index=True),   # ip:<adresas> arba email:<sha256>
    Column("ts", Float, nullable=False),
)

hr_outbox = Table(
    "hr_outbox", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("to_employee_id", Integer, nullable=False),
    Column("to_email", String(160), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("subject", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("link", String(300)),
    Column("sent_via", String(16), nullable=False, default="outbox"),
)
