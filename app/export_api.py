"""Mašininis Rivilė GAMA EIP eksportas Rivilės serveriui (VM: uno_import.bat + suplanuota užduotis).

GET  /api/export/gama-eip               paštas -> tik dar nepatvirtintos paruoštos sąskaitos kaip EIP.
     ?format=zip                        ZIP: uno.eip + kiekvienos sąskaitos originalus PDF (<numeris>.pdf).
                                        204 = naujų nėra. Antraštės: X-UNO-Batch, X-UNO-Count.
POST /api/export/gama-eip/ack?batch=ID  VM išsaugojo failą: tos siuntos sąskaitos daugiau nebesiunčiamos.

Kol siunta nepatvirtinta, tos pačios sąskaitos grąžinamos vėl (nutrūkęs atsisiuntimas nieko nepameta),
o patvirtintos niekada nesiunčiamos antrą kartą (Rivilėje nebus dublikatų).
Prieiga tik su X-UNO-Token (aplinkos kintamasis UNO_EXPORT_TOKEN); be jo maršrutai išjungti.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import zipfile
from pathlib import Path
import threading
from datetime import timezone

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite

from app import services
from core import rivile_gama_eip
from core.hr import db as hr_db

router = APIRouter()
TOKEN_ENV = "UNO_EXPORT_TOKEN"
HEADER = "X-UNO-Token"

metadata = MetaData()
exported = Table(
    "uno_eip_export", metadata,
    Column("key", String(200), primary_key=True),          # tiekėjo kodas | sąskaitos numeris
    Column("batch", String(32), nullable=False, index=True),
    Column("acked", Boolean, nullable=False, default=False),
    Column("created", DateTime(timezone=True), server_default=func.now()),
)
pings = Table(   # kiekvienas Rivilės serverio kreipimasis: UI rodo, kada VM paskutinį kartą pingino
    "uno_export_ping", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", DateTime(timezone=True), server_default=func.now(), index=True),
    Column("kind", String(10), nullable=False),        # get | ack
    Column("status", Integer, nullable=False),
    Column("count", Integer, nullable=False, default=0),
    Column("batch", String(32), nullable=False, default=""),
)
_ready: set[str] = set()
_lock = threading.Lock()


def _engine():
    engine = hr_db.get_engine()
    with _lock:
        if engine.url.render_as_string() not in _ready:
            metadata.create_all(engine)
            _ready.add(engine.url.render_as_string())
    return engine


def _require_token(request: Request) -> None:
    expected = os.environ.get(TOKEN_ENV, "").strip()
    given = request.headers.get(HEADER, "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Netinkamas raktas")


def _ping(kind: str, status: int, count: int = 0, batch: str = "") -> None:
    with _engine().begin() as conn:
        conn.execute(insert(pings).values(kind=kind, status=status, count=count, batch=batch))


def _iso(row) -> str | None:
    if not row or row["at"] is None:
        return None
    at = row["at"]
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def last_ping() -> dict:
    """UI: paskutinis Rivilės serverio kreipimasis ir paskutinė patvirtinta siunta."""
    with _engine().begin() as conn:
        last = conn.execute(select(pings).order_by(pings.c.id.desc()).limit(1)).mappings().first()
        acked = conn.execute(select(pings).where(pings.c.kind == "ack").order_by(pings.c.id.desc()).limit(1)
                             ).mappings().first()
        total = conn.execute(select(func.count()).select_from(exported).where(exported.c.acked)).scalar_one()
    return {"last_at": _iso(last), "last_kind": last["kind"] if last else None,
            "last_status": last["status"] if last else None, "last_count": last["count"] if last else 0,
            "last_ack_at": _iso(acked), "last_ack_count": acked["count"] if acked else 0, "acked_total": total}


def pdf_name(p) -> str:
    """Saugus PDF vardas pagal sąskaitos numerį (be kelio skirtukų)."""
    stem = re.sub(r"[^\w.\-]", "_", p.number or "saskaita", flags=re.UNICODE).strip("._") or "saskaita"
    return stem[:80] + ".pdf"


def _zip(eip_xml: bytes, invoices) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("uno.eip", eip_xml)
        for p in invoices:
            src = Path(p.source_file)
            if not src.is_file():
                continue
            name = pdf_name(p)
            while name in used:
                name = name[:-4] + "_1.pdf"
            used.add(name)
            zf.write(src, name)
    return buf.getvalue()


def _key(p) -> str:
    return f"{p.supplier_code}|{p.number}"


def _insert(engine):
    dialect = postgresql if engine.dialect.name == "postgresql" else sqlite
    return dialect.insert(exported)


@router.get("/api/export/gama-eip")
def export_gama_eip(request: Request, format: str = "eip"):
    _require_token(request)
    result = services.run_invoices_mail()
    engine = _engine()
    with engine.begin() as conn:
        acked = set(conn.execute(select(exported.c.key).where(exported.c.acked)).scalars())
    pending = [p for p in result.get("_export", []) if _key(p) not in acked]
    eip = rivile_gama_eip.build_eip(pending)
    included = [p for p in pending if p.number in set(eip.included)]
    if not included:
        _ping("get", 204)
        return Response(status_code=204)
    keys = sorted(_key(p) for p in included)
    batch = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]
    with engine.begin() as conn:
        for key in keys:
            stmt = _insert(engine).values(key=key, batch=batch, acked=False)
            conn.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"batch": batch},
                                                    where=~exported.c.acked))
    _ping("get", 200, len(included), batch)
    if format == "zip":
        return Response(_zip(eip.xml, included), media_type="application/zip", headers={
            "X-UNO-Batch": batch, "X-UNO-Count": str(len(included)),
            "Content-Disposition": f'attachment; filename="uno_{batch}.zip"'})
    return Response(eip.xml, media_type="application/octet-stream", headers={
        "X-UNO-Batch": batch, "X-UNO-Count": str(len(included)),
        "Content-Disposition": f'attachment; filename="uno_{batch}.eip"'})


@router.post("/api/export/gama-eip/ack")
def ack_gama_eip(request: Request, batch: str):
    _require_token(request)
    if not batch.isalnum() or len(batch) > 32:
        raise HTTPException(status_code=400, detail="Netinkamas siuntos ID")
    with _engine().begin() as conn:
        done = conn.execute(update(exported).where(exported.c.batch == batch, ~exported.c.acked)
                            .values(acked=True)).rowcount
    _ping("ack", 200, done, batch)
    return {"acked": done}
