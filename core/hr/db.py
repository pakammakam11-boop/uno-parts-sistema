"""DB prieiga: SQLAlchemy Core variklis iš HR_DATABASE_URL arba DATABASE_URL (Neon Postgres), kitaip SQLite data/hr.db."""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.pool import NullPool

from core.hr.errors import HrError
from core.hr.schema import metadata

DEFAULT_SQLITE = Path(__file__).resolve().parents[2] / "data" / "hr.db"
_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def normalize_url(url: str) -> str:
    """Neon duoda postgres:// arba postgresql://; SQLAlchemy su psycopg 3 reikia postgresql+psycopg://."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def database_url() -> str:
    url = os.environ.get("HR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return normalize_url(url)
    DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE}"


def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, poolclass=NullPool, connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_conn, _record):
            dbapi_conn.isolation_level = None  # pysqlite pats nepradeda transakcijų: tai daro "begin" įvykis
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):
            # SQLite neturi SELECT ... FOR UPDATE: rašymo užraktas imamas transakcijos pradžioje,
            # todėl būsenos skaitymas ir rašymas (postback, atšaukimas, kūrimas) vyksta nuosekliai.
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return engine
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True)  # serverless: jokio jungčių baseino


def get_engine() -> Engine:
    """Variklis pagal dabartinę aplinką; lentelės sukuriamos pirmo naudojimo metu."""
    url = database_url()
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            engine = _build_engine(url)
            metadata.create_all(engine)
            _engines[url] = engine
        return engine


def conn_ctx(conn: Connection) -> dict:
    """Transakcijos kontekstas: base_url ir veiksmai po commit (laiškų siuntimas)."""
    if not hasattr(conn, "hr_ctx"):
        conn.hr_ctx = {"base_url": "", "after_commit": []}
    return conn.hr_ctx


@contextmanager
def tx(base_url: str = "") -> Iterator[Connection]:
    """Viena transakcija. Po sėkmingo commit paleidžiami after_commit veiksmai (laiškų siuntimas).

    HrError su commit=True išsaugo jau atliktus įrašus (pvz. audito eilutę apie klastotę) ir tik tada perduoda klaidą.
    """
    conn = get_engine().connect()
    ctx = conn_ctx(conn)
    ctx["base_url"] = base_url
    trans = conn.begin()
    try:
        yield conn
    except HrError as e:
        if e.commit:
            trans.commit()
        else:
            trans.rollback()
        raise
    except BaseException:
        trans.rollback()
        raise
    else:
        trans.commit()
    finally:
        conn.close()
    for callback in ctx["after_commit"]:
        callback()
