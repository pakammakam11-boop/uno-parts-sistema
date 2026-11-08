import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from core.period import Period, easter_sunday, lt_holidays
from core.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "uno.db")


def test_tenant_isolation(store):
    a, b = store.add_company("A"), store.add_company("B")
    up = store.save_upload(a, "2026-10", "timesheet", "t.xlsx", b"data", "ana")
    run = store.save_run(a, "2026-10", "payroll", "1", [up["id"]], {"x": 1})
    assert store.get_upload(b, up["id"]) is None
    assert store.list_uploads(b, "2026-10") == []
    assert store.get_run(b, run) is None
    assert store.latest_run(b, "2026-10", "payroll") is None
    assert store.list_audit(b) == []
    with pytest.raises(KeyError):
        store.read_upload(b, up["id"])
    assert store.read_upload(a, up["id"]) == b"data"


@pytest.mark.parametrize("name", ["../../evil.txt", "..\\..\\evil.txt", "/etc/passwd", "a/b/../../c.xlsx"])
def test_path_traversal_blocked(store, tmp_path, name):
    c = store.add_company("A")
    up = store.save_upload(c, "2026-10", "timesheet", name, b"x", "ana")
    root = (tmp_path / "uploads").resolve()
    assert root in Path(up["stored_path"]).resolve().parents
    assert not (tmp_path / "evil.txt").exists()


def test_bad_period_and_empty_name_rejected(store):
    c = store.add_company("A")
    with pytest.raises(ValueError):
        store.save_upload(c, "../2026", "k", "a.txt", b"x", "u")
    with pytest.raises(ValueError):
        store.save_upload(c, "2026-10", "k", "..", b"x", "u")


def test_decimal_serialized_as_string(store):
    c = store.add_company("A")
    run = store.save_run(c, "2026-10", "payroll", "1", [], {"gross": Decimal("1234.50")})
    assert store.get_run(c, run)["result"] == {"gross": "1234.50"}


def test_workdays_oct_and_nov_2026():
    assert Period.parse("2026-10").workdays() == 22
    # Lapkritis 2026: 21 darbo savaitės diena, minus Vėlinių pirmadienis (11-02) = 20
    assert Period.parse("2026-11").workdays() == 20


def test_easter_and_holidays():
    assert easter_sunday(2026) == date(2026, 4, 5)
    holidays = lt_holidays(2026)
    assert date(2026, 4, 6) in holidays and date(2026, 11, 2) in holidays


def test_period_basics():
    p = Period.parse("2026-10")
    assert (p.start, p.end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert p.label_lt == "2026 m. spalis" and str(p) == "2026-10"
    with pytest.raises(ValueError):
        Period.parse("2026-13")


# ---------- audito pataisos: santykiniai keliai, audito laikotarpis ----------

def test_stored_path_is_relative_and_resolved_on_read(store, tmp_path):
    import sqlite3
    c = store.add_company("A")
    up = store.save_upload(c, "2026-10", "timesheet", "t.xlsx", b"data", "ana")
    raw = sqlite3.connect(store.db_path).execute("SELECT stored_path FROM uploads").fetchone()[0]
    assert not Path(raw).is_absolute() and raw.startswith(f"{c}/2026-10/")
    assert Path(up["stored_path"]).is_absolute() and Path(up["stored_path"]).read_bytes() == b"data"
    # tas pats DB failas ir upload_root iš naujo atidarytas: santykinis kelias vis dar veikia
    assert Store(store.db_path, upload_root=store.upload_root).read_upload(c, up["id"]) == b"data"


def test_relative_path_cannot_escape_upload_root(store):
    import sqlite3
    c = store.add_company("A")
    up = store.save_upload(c, "2026-10", "timesheet", "t.xlsx", b"data", "ana")
    con = sqlite3.connect(store.db_path)
    with con:
        con.execute("UPDATE uploads SET stored_path='../../evil.txt' WHERE id=?", (up["id"],))
    con.close()
    with pytest.raises(ValueError):
        store.get_upload(c, up["id"])


def test_legacy_absolute_stored_path_still_works(store, tmp_path):
    import sqlite3
    c = store.add_company("A")
    up = store.save_upload(c, "2026-10", "timesheet", "t.xlsx", b"data", "ana")
    absolute = str(Path(up["stored_path"]))
    con = sqlite3.connect(store.db_path)
    with con:
        con.execute("UPDATE uploads SET stored_path=? WHERE id=?", (absolute, up["id"]))
    con.close()
    assert store.read_upload(c, up["id"]) == b"data"


def test_audit_period_filter_and_limit(store):
    c = store.add_company("A")
    for i in range(505):
        store.log(c, "u", "x", f"n{i}", "2026-10" if i % 2 else "2026-09")
    assert len(store.list_audit(c)) == 500
    assert store.list_audit(c)[-1]["detail"] == "n504"            # naujausi, seniausias pirmas
    assert all(e["period"] == "2026-09" for e in store.list_audit(c, period="2026-09"))
    assert len(store.list_audit(c, limit=5000)) == 500


def test_audit_period_column_migrated_for_old_db(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE audit (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, user TEXT NOT NULL,"
                      " action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL);"
                      "INSERT INTO audit VALUES (1, 1, 'u', 'run', 'timesheet 2026-10 run#1 v1', 't');")
    con.commit()
    con.close()
    store = Store(db)
    assert store.list_audit(1, period="2026-10")[0]["id"] == 1
    Store(db)                                                       # antras atidarymas nieko nelūžta


def test_legacy_cwd_relative_stored_path_is_migrated(tmp_path, monkeypatch):
    """S15: seniau saugota 'data/uploads/...' (nuo cwd) turi rastis, o ne virsti data/uploads/data/uploads/..."""
    monkeypatch.chdir(tmp_path)
    store = Store("data/uno.db")
    cid = store.add_company("A")
    up = store.save_upload(cid, "2026-10", "timesheet", "t.xlsx", b"abc", "ana")
    legacy = "data/uploads/" + up["stored_path"].replace("\\", "/").split("/uploads/")[-1]
    con = store._conn()
    with con:
        con.execute("UPDATE uploads SET stored_path=? WHERE id=?", (legacy, up["id"]))
        con.execute("INSERT INTO uploads(company_id, period, kind, filename, sha256, stored_path, uploaded_by,"
                    " uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (cid, "2026-10", "x", "x", "0", "../../outside.txt", "ana", "t"))
    con.close()
    reopened = Store("data/uno.db")
    assert reopened.read_upload(cid, up["id"]) == b"abc"
    assert reopened.get_upload(cid, up["id"])["stored_path"].endswith(f"uploads{os.sep}{cid}{os.sep}2026-10"
                                                                      f"{os.sep}{Path(legacy).name}")
    with pytest.raises(ValueError):
        reopened.list_uploads(cid, "2026-10", "x")


def test_legacy_prefix_migrated_independent_of_cwd_s15(tmp_path, monkeypatch):
    import sqlite3
    from core.store import Store
    root = tmp_path / "restored"
    store = Store(str(root / "uno.db"))
    cid = store.add_company("A")
    up = store.save_upload(cid, "2026-10", "timesheet", "t.xlsx", b"abc", "u")
    rel = sqlite3.connect(root / "uno.db").execute("SELECT stored_path FROM uploads").fetchone()[0]
    con = sqlite3.connect(root / "uno.db")
    con.execute("UPDATE uploads SET stored_path=?", ("data/uploads/" + rel,))
    con.commit(); con.close()
    monkeypatch.chdir(tmp_path)  # kitas darbo katalogas nei sukūrimo metu
    again = Store(str(root / "uno.db"))
    assert again.read_upload(cid, up["id"] if isinstance(up, dict) else up) == b"abc"
