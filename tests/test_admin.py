"""app.admin: atsarginė kopija -> patikra -> atkūrimas."""
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app import admin
from core.store import Store

CONTENT = b"abc,123\n\xc4\x85"


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    store = Store(d / "uno.db")
    cid = store.add_company("UAB Testas")
    store.save_upload(cid, "2026-01", "bank", "israsas.csv", CONTENT, "tester")
    return d


def _rows(db):
    con = sqlite3.connect(db)
    try:
        return (con.execute("SELECT id, name FROM companies").fetchall(),
                con.execute("SELECT id, sha256, filename FROM uploads").fetchall())
    finally:
        con.close()


def _uploads(d):
    return {p.relative_to(d / "uploads").as_posix(): p.read_bytes()
            for p in (d / "uploads").rglob("*") if p.is_file()}


def _rewrite_zip(src_zip, dst_zip, mutate, extra=None):
    with zipfile.ZipFile(src_zip) as zin, zipfile.ZipFile(dst_zip, "w") as zout:
        for i in zin.infolist():
            zout.writestr(i.filename, mutate(i.filename, zin.read(i.filename)))
        if extra:
            zout.writestr(*extra)


def test_roundtrip(src, tmp_path):
    z = admin.make_backup(src / "uno.db", tmp_path / "out")
    assert admin.verify_backup(z)["rules_version"]
    target = tmp_path / "new"
    admin.restore_backup(z, target)
    assert _rows(target / "uno.db") == _rows(src / "uno.db")
    assert _uploads(target) == _uploads(src) and _uploads(src)
    con = sqlite3.connect(target / "uno.db")
    stored = con.execute("SELECT stored_path FROM uploads").fetchone()[0]
    con.close()
    # stored_path dabar santykinis nuo uploads šaknies (perkeliamas paketas)
    path = Path(stored) if Path(stored).is_absolute() else target / "uploads" / stored
    assert path.read_bytes() == CONTENT
    assert path.resolve().is_relative_to((target / "uploads").resolve())


def test_tampered_zip_rejected(src, tmp_path):
    z = admin.make_backup(src / "uno.db", tmp_path / "out")
    bad = tmp_path / "bad.zip"
    _rewrite_zip(z, bad, lambda n, d: d + b"x" if n.startswith("uploads/") else d)
    with pytest.raises(admin.AdminError, match="Kontrolinė suma"):
        admin.verify_backup(bad)
    with pytest.raises(admin.AdminError):
        admin.restore_backup(bad, tmp_path / "t")
    assert not (tmp_path / "t" / "uno.db").exists()


def test_extra_file_rejected(src, tmp_path):
    z = admin.make_backup(src / "uno.db", tmp_path / "out")
    bad = tmp_path / "extra.zip"
    _rewrite_zip(z, bad, lambda n, d: d, extra=("uploads/papildomas.txt", b"x"))
    with pytest.raises(admin.AdminError, match="nėra manifeste"):
        admin.verify_backup(bad)


@pytest.mark.parametrize("evil", ["../evil.txt", "uploads/../../evil.txt", "/abs.txt",
                                  "C:/x.txt", "uploads\\..\\e.txt"])
def test_zip_slip_rejected(src, tmp_path, evil):
    z = admin.make_backup(src / "uno.db", tmp_path / "out")
    bad = tmp_path / "slip.zip"

    def mut(name, data):
        if name == admin.MANIFEST:
            m = json.loads(data)
            m["files"].append({"path": evil, "sha256": "0" * 64, "size": 1})
            return json.dumps(m).encode()
        return data
    _rewrite_zip(z, bad, mut, extra=(evil, b"x"))
    target = tmp_path / "t"
    with pytest.raises(admin.AdminError):
        admin.restore_backup(bad, target)
    assert not (tmp_path / "evil.txt").exists() and not (target / "uno.db").exists()


def test_restore_refuses_overwrite_without_force(src, tmp_path):
    z = admin.make_backup(src / "uno.db", tmp_path / "out")
    target = tmp_path / "t"
    admin.restore_backup(z, target)
    before = (target / "uno.db").read_bytes()
    with pytest.raises(admin.AdminError, match="--force"):
        admin.restore_backup(z, target)
    assert (target / "uno.db").read_bytes() == before
    admin.restore_backup(z, target, force=True)
    assert any(p.name.startswith("uno.db.prieš") for p in target.iterdir())


def test_init_and_cli(tmp_path):
    db = tmp_path / "d" / "uno.db"
    assert admin.main(["init", "--company", "UAB X", "--db", str(db)]) == 0
    assert admin.main(["init", "--company", "UAB Y", "--db", str(db)]) == 0
    assert _rows(db)[0] == [(1, "UAB X")]
    assert admin.main(["backup", "--out", str(tmp_path / "o"), "--db", str(db)]) == 0
    z = next((tmp_path / "o").glob("*.zip"))
    assert admin.main(["verify", str(z)]) == 0
    assert admin.main(["verify", str(tmp_path / "nera.zip")]) == 1
