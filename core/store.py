"""SQLite saugykla: įmonės, įkėlimai, skaičiavimai, patvirtinimai, auditas.

Kiekviena funkcija priima company_id ir kiekviena užklausa filtruoja pagal jį (multi-tenant).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, period TEXT NOT NULL, kind TEXT NOT NULL,
  filename TEXT NOT NULL, sha256 TEXT NOT NULL, stored_path TEXT NOT NULL,
  uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, period TEXT NOT NULL, module TEXT NOT NULL,
  rules_version TEXT NOT NULL, input_upload_ids TEXT NOT NULL, result_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, period TEXT NOT NULL, module TEXT NOT NULL,
  run_id INTEGER NOT NULL, input_sha256 TEXT NOT NULL, approved_by TEXT NOT NULL,
  approved_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, user TEXT NOT NULL, action TEXT NOT NULL,
  detail TEXT NOT NULL, at TEXT NOT NULL, period TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS gama_runs (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, month TEXT NOT NULL, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, summary_json TEXT NOT NULL,
  findings_json TEXT NOT NULL, xlsx_name TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_gama_runs ON gama_runs(company_id, month);
CREATE INDEX IF NOT EXISTS ix_uploads ON uploads(company_id, period, kind);
CREATE INDEX IF NOT EXISTS ix_runs ON runs(company_id, period, module);
CREATE INDEX IF NOT EXISTS ix_approvals ON approvals(company_id, period, module);
"""

AUDIT_LIMIT = 500
_PERIOD_RE = re.compile(r"\b(\d{4}-\d{2})\b")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def to_json(value) -> str:
    """JSON, kuriame Decimal paverčiamas eilute (be float paklaidų)."""
    def default(o):
        if isinstance(o, Decimal):
            return str(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        raise TypeError(f"Netinkamas JSON tipas: {type(o).__name__}")
    return json.dumps(value, default=default, ensure_ascii=False)


def safe_filename(name: str) -> str:
    """Palieka tik failo vardą be kelio dalių ir keistų simbolių."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[^\w.\-]", "_", base, flags=re.UNICODE).lstrip(".")
    if not base:
        raise ValueError("Tuščias arba neleistinas failo vardas")
    return base[:120]


def _safe_part(value) -> str:
    if not re.fullmatch(r"[\w\-]+", str(value)):
        raise ValueError(f"Neleistina kelio dalis: {value!r}")
    return str(value)


class Store:
    def __init__(self, db_path: str | Path = "data/uno.db", upload_root: str | Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_root = Path(upload_root) if upload_root else self.db_path.parent / "uploads"
        con = self._conn()
        try:
            with con:
                con.executescript(SCHEMA)
                self._migrate_audit_period(con)
                self._migrate_legacy_paths(con)
        finally:
            con.close()

    @staticmethod
    def _migrate_audit_period(con: sqlite3.Connection) -> None:
        """Senose DB audit lentelė neturi period stulpelio: pridedame ir užpildome iš detalių."""
        columns = {r[1] for r in con.execute("PRAGMA table_info(audit)")}
        if "period" in columns:
            return
        con.execute("ALTER TABLE audit ADD COLUMN period TEXT NOT NULL DEFAULT ''")
        for row_id, detail in con.execute("SELECT id, detail FROM audit").fetchall():
            found = _PERIOD_RE.search(detail or "")
            if found:
                con.execute("UPDATE audit SET period=? WHERE id=?", (found.group(1), row_id))

    def _migrate_legacy_paths(self, con: sqlite3.Connection) -> None:
        """Seniau santykinis stored_path buvo saugomas nuo darbo katalogo ('data/uploads/1/...'): perrašome
        į kelią nuo upload_root. Kas po perrašymo vis tiek išeitų už upload_root, paliekama ir atmetama skaitant."""
        root = self.upload_root.resolve()
        for row_id, stored in con.execute("SELECT id, stored_path FROM uploads").fetchall():
            if Path(stored).is_absolute() or (root / stored).exists():
                continue
            fixed = _strip_legacy_prefix(stored)
            if fixed and (root / fixed).exists():  # nepriklauso nuo darbo katalogo (pvz. po atkūrimo kitur)
                con.execute("UPDATE uploads SET stored_path=? WHERE id=?", (fixed, row_id))
                continue
            legacy = (Path.cwd() / stored).resolve()
            if root in legacy.parents:
                con.execute("UPDATE uploads SET stored_path=? WHERE id=?",
                            (legacy.relative_to(root).as_posix(), row_id))

    def _resolve_path(self, stored: str) -> str:
        """Santykinį kelią sprendžiame nuo upload_root ir neleidžiame išeiti iš jo; senus absoliučius paliekame."""
        if Path(stored).is_absolute():
            return stored
        root = self.upload_root.resolve()
        path = (root / stored).resolve()
        if root not in path.parents:
            raise ValueError("Išsaugotas failo kelias išeina už įkėlimų katalogo")
        return str(path)

    def _with_path(self, row: dict) -> dict:
        row["stored_path"] = self._resolve_path(row["stored_path"])
        return row

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _query(self, sql: str, args: tuple = ()) -> list[dict]:
        con = self._conn()
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def _write(self, sql: str, args: tuple) -> tuple[int, int]:
        """Grąžina (lastrowid, rowcount)."""
        con = self._conn()
        try:
            with con:
                cur = con.execute(sql, args)
                return cur.lastrowid, cur.rowcount
        finally:
            con.close()

    def _insert(self, sql: str, args: tuple) -> int:
        return self._write(sql, args)[0]

    # --- įmonės ---
    def add_company(self, name: str) -> int:
        return self._insert("INSERT INTO companies(name) VALUES (?)", (name,))

    def get_company(self, company_id: int) -> dict | None:
        rows = self._query("SELECT * FROM companies WHERE id=?", (company_id,))
        return rows[0] if rows else None

    # --- įkėlimai ---
    def save_upload(self, company_id: int, period: str, kind: str, filename: str,
                    content: bytes, uploaded_by: str) -> dict:
        period = str(period)
        digest = hashlib.sha256(content).hexdigest()
        directory = self.upload_root / _safe_part(company_id) / _safe_part(period)
        path = directory / f"{digest}_{safe_filename(filename)}"
        if directory.resolve() not in path.resolve().parents:
            raise ValueError("Failo kelias išeina už įkėlimų katalogo")
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        upload_id = self._insert(
            "INSERT INTO uploads(company_id, period, kind, filename, sha256, stored_path,"
            " uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
            (company_id, period, kind, filename, digest, path.relative_to(self.upload_root).as_posix(),
             uploaded_by, now_iso()))
        self.log(company_id, uploaded_by, "upload", f"{kind} {period} #{upload_id} {digest[:12]}", period)
        return self.get_upload(company_id, upload_id)

    def get_upload(self, company_id: int, upload_id: int) -> dict | None:
        rows = self._query("SELECT * FROM uploads WHERE company_id=? AND id=?", (company_id, upload_id))
        return self._with_path(rows[0]) if rows else None

    def list_uploads(self, company_id: int, period: str, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM uploads WHERE company_id=? AND period=?"
        args: list = [company_id, str(period)]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return [self._with_path(r) for r in self._query(sql + " ORDER BY id", tuple(args))]

    def read_upload(self, company_id: int, upload_id: int) -> bytes:
        row = self.get_upload(company_id, upload_id)
        if row is None:
            raise KeyError(f"Įkėlimas {upload_id} nerastas šiai įmonei")
        return Path(row["stored_path"]).read_bytes()

    # --- skaičiavimai ---
    def save_run(self, company_id: int, period: str, module: str, rules_version: str,
                 input_upload_ids: list[int], result) -> int:
        return self._insert(
            "INSERT INTO runs(company_id, period, module, rules_version, input_upload_ids,"
            " result_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (company_id, str(period), module, rules_version,
             json.dumps(sorted(input_upload_ids)), to_json(result), now_iso()))

    def get_run(self, company_id: int, run_id: int) -> dict | None:
        rows = self._query("SELECT * FROM runs WHERE company_id=? AND id=?", (company_id, run_id))
        if not rows:
            return None
        row = rows[0]
        row["input_upload_ids"] = json.loads(row["input_upload_ids"])
        row["result"] = json.loads(row["result_json"])
        return row

    def latest_run(self, company_id: int, period: str, module: str) -> dict | None:
        rows = self._query("SELECT id FROM runs WHERE company_id=? AND period=? AND module=?"
                           " ORDER BY id DESC LIMIT 1", (company_id, str(period), module))
        return self.get_run(company_id, rows[0]["id"]) if rows else None

    # --- patvirtinimai ---
    def add_approval(self, company_id: int, period: str, module: str, run_id: int,
                     input_sha256: str, approved_by: str) -> int:
        return self._insert(
            "INSERT INTO approvals(company_id, period, module, run_id, input_sha256,"
            " approved_by, approved_at) VALUES (?,?,?,?,?,?,?)",
            (company_id, str(period), module, run_id, input_sha256, approved_by, now_iso()))

    def active_approval(self, company_id: int, period: str, module: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM approvals WHERE company_id=? AND period=? AND module=?"
            " AND revoked_at IS NULL ORDER BY id DESC LIMIT 1", (company_id, str(period), module))
        return rows[0] if rows else None

    def revoke_approvals(self, company_id: int, period: str, module: str) -> int:
        return self._write(
            "UPDATE approvals SET revoked_at=? WHERE company_id=? AND period=? AND module=?"
            " AND revoked_at IS NULL", (now_iso(), company_id, str(period), module))[1]

    # --- GAMA mokesčių patikros ---
    def add_gama_run(self, company_id: int, month: str, created_by: str, snapshot_sha256: str,
                     summary, findings, xlsx_name: str) -> int:
        return self._insert(
            "INSERT INTO gama_runs(company_id, month, created_by, created_at, snapshot_sha256,"
            " summary_json, findings_json, xlsx_name) VALUES (?,?,?,?,?,?,?,?)",
            (company_id, month, created_by, now_iso(), snapshot_sha256, to_json(summary),
             to_json(findings), xlsx_name))

    @staticmethod
    def _gama_row(row: dict, with_findings: bool) -> dict:
        row["summary"] = json.loads(row.pop("summary_json"))
        findings = json.loads(row.pop("findings_json"))
        if with_findings:
            row["findings"] = findings
        return row

    def get_gama_run(self, company_id: int, run_id: int) -> dict | None:
        rows = self._query("SELECT * FROM gama_runs WHERE company_id=? AND id=?", (company_id, run_id))
        return self._gama_row(rows[0], True) if rows else None

    def list_gama_runs(self, company_id: int, month: str | None = None, limit: int = 50) -> list[dict]:
        """Naujausi pirmi, be radinių (sąrašui)."""
        sql, args = "SELECT * FROM gama_runs WHERE company_id=?", [company_id]
        if month:
            sql += " AND month=?"
            args.append(month)
        rows = self._query(sql + " ORDER BY id DESC LIMIT ?", (*args, min(int(limit), 200)))
        return [self._gama_row(r, False) for r in rows]

    # --- auditas ---
    def log(self, company_id: int, user: str, action: str, detail: str = "", period: str = "") -> int:
        return self._insert(
            "INSERT INTO audit(company_id, user, action, detail, at, period) VALUES (?,?,?,?,?,?)",
            (company_id, user, action, detail, now_iso(), str(period)))

    def list_audit(self, company_id: int, action: str | None = None, period: str | None = None,
                   limit: int = AUDIT_LIMIT) -> list[dict]:
        """Naujausi `limit` įrašų (ne daugiau 500), grąžinami seniausias pirmas."""
        sql, args = "SELECT * FROM audit WHERE company_id=?", [company_id]
        if action:
            sql += " AND action=?"
            args.append(action)
        if period:
            sql += " AND period=?"
            args.append(str(period))
        rows = self._query(sql + " ORDER BY id DESC LIMIT ?", (*args, min(int(limit), AUDIT_LIMIT)))
        return rows[::-1]


LEGACY_UPLOAD_PREFIX = "data/uploads/"


def _strip_legacy_prefix(stored: str) -> str | None:
    """Senas formatas 'data/uploads/<įmonė>/...' -> '<įmonė>/...' (kelias nuo upload_root)."""
    norm = stored.replace("\\", "/")
    if norm.startswith(LEGACY_UPLOAD_PREFIX) and ".." not in Path(norm).parts:
        return norm[len(LEGACY_UPLOAD_PREFIX):]
    return None
