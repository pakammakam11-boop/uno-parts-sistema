"""UNO administravimas: atsarginė kopija, atkūrimas, patikra, pradinė įmonė.

  py -3.12 -m app.admin backup --out atsargines
  py -3.12 -m app.admin verify atsargines/uno-backup-....zip
  py -3.12 -m app.admin restore atsargines/uno-backup-....zip --target data [--force]
  py -3.12 -m app.admin init --company "UAB Pavadinimas"

DB kopijuojama SQLite backup API (nuosekli net veikiant serveriui), ne failo kopija.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "manifest.json"
DB_ENTRY = "uno.db"
UPLOADS_ENTRY = "uploads"
FORMAT_VERSION = 1


class AdminError(Exception):
    """Klaida su žmogui suprantamu (LT) pranešimu."""


def default_db() -> Path:
    return Path(os.environ.get("UNO_DB") or ROOT / "data" / "uno.db")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def app_version() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "nežinoma"
    except (OSError, subprocess.SubprocessError):
        return "nežinoma"


def rules_version() -> str:
    try:
        text = (ROOT / "app" / "api_v1.py").read_text(encoding="utf-8")
    except OSError:
        return "nežinoma"
    m = re.search(r'^RULES_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "nežinoma"


def safe_member(name: str) -> PurePosixPath:
    """Zip įrašo vardas: tik santykinis, be '..', be disko raidės, be atbulinių brūkšnių."""
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise AdminError(f"Pavojingas kelias archyve: {name!r}")
    p = PurePosixPath(name)
    if any(part in ("..", "") for part in p.parts):
        raise AdminError(f"Pavojingas kelias archyve: {name!r}")
    return p


def _sqlite_snapshot(db: Path, dest: Path) -> None:
    src = sqlite3.connect(db)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _collect_uploads(uploads: Path) -> list[Path]:
    if not uploads.is_dir():
        return []
    return sorted(p for p in uploads.rglob("*") if p.is_file())


def _build_manifest(files: dict[str, Path], uploads: Path) -> dict:
    return {
        "format": FORMAT_VERSION, "created_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": app_version(), "rules_version": rules_version(),
        "uploads_root": str(uploads.resolve()),
        "files": [{"path": n, "sha256": sha256_file(p), "size": p.stat().st_size}
                  for n, p in files.items()],
    }


def make_backup(db: Path, out_dir: Path, uploads: Path | None = None) -> Path:
    if not db.is_file():
        raise AdminError(f"Duomenų bazė nerasta: {db}")
    uploads = uploads or db.parent / "uploads"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"uno-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_ENTRY
        _sqlite_snapshot(db, snap)
        files = {DB_ENTRY: snap}
        for p in _collect_uploads(uploads):
            files[f"{UPLOADS_ENTRY}/{p.relative_to(uploads).as_posix()}"] = p
        manifest = _build_manifest(files, uploads)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, p in files.items():
                zf.write(p, name)
    return zip_path


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        return json.loads(zf.read(MANIFEST).decode("utf-8"))
    except (KeyError, ValueError) as exc:
        raise AdminError("Archyve nėra galiojančio manifest.json") from exc


def _hash_member(zf: zipfile.ZipFile, name: str) -> str:
    h = hashlib.sha256()
    with zf.open(name) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_members(zf: zipfile.ZipFile, manifest: dict) -> None:
    expected = {}
    for entry in manifest.get("files", []):
        safe_member(entry["path"])
        expected[entry["path"]] = entry["sha256"]
    if DB_ENTRY not in expected:
        raise AdminError("Archyve nėra duomenų bazės (uno.db)")
    actual = {i.filename for i in zf.infolist() if not i.is_dir()} - {MANIFEST}
    for name in sorted(actual - set(expected)):
        raise AdminError(f"Archyve yra failas, kurio nėra manifeste: {name!r}")
    for name, digest in expected.items():
        if name not in actual:
            raise AdminError(f"Archyve trūksta failo: {name}")
        if _hash_member(zf, name) != digest:
            raise AdminError(f"Kontrolinė suma nesutampa (failas pakeistas): {name}")


def verify_backup(zip_path: Path) -> dict:
    """Patikrina visų failų SHA-256 ir kad archyve nėra papildomų failų. Grąžina manifestą."""
    if not zip_path.is_file():
        raise AdminError(f"Archyvas nerastas: {zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise AdminError("Failas nėra tinkamas zip archyvas") from exc
    with zf:
        manifest = _read_manifest(zf)
        _check_members(zf, manifest)
    return manifest


def _rewrite_stored_paths(db: Path, old_root: str, new_root: Path) -> int:
    """Absoliučius stored_path, rodančius į seną uploads aplanką, nukreipia į naują vietą.

    Santykiniai keliai (relative to upload_root) lieka nepakeisti.
    """
    con = sqlite3.connect(db)
    changed = 0
    try:
        with con:
            for pid, sp in con.execute("SELECT id, stored_path FROM uploads").fetchall():
                if os.path.isabs(sp) and sp.startswith(old_root):
                    rel = Path(sp[len(old_root):].lstrip("\\/"))
                    con.execute("UPDATE uploads SET stored_path=? WHERE id=?",
                                (str(new_root / rel), pid))
                    changed += 1
    finally:
        con.close()
    return changed


def _extract_all(zip_path: Path, manifest: dict, base: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        for entry in manifest["files"]:
            rel = safe_member(entry["path"])
            dest = (base / Path(*rel.parts)).resolve()
            if base not in dest.parents:
                raise AdminError(f"Pavojingas kelias archyve: {entry['path']!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry["path"]) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def restore_backup(zip_path: Path, target: Path, force: bool = False) -> dict:
    manifest = verify_backup(zip_path)
    db_target = target / DB_ENTRY
    if db_target.exists() and not force:
        raise AdminError(f"{db_target} jau yra. Nieko neperrašyta; naudokite --force, jei tikrai "
                         "norite pakeisti (senoji DB bus išsaugota su priesaga .prieš-atkūrimą).")
    target.mkdir(parents=True, exist_ok=True)
    if db_target.exists():
        stamp = datetime.now().strftime(".prieš-atkūrimą-%Y%m%d-%H%M%S")
        shutil.move(str(db_target), str(db_target) + stamp)
    base = target.resolve()
    _extract_all(zip_path, manifest, base)
    manifest["paths_rewritten"] = _rewrite_stored_paths(
        db_target, manifest.get("uploads_root", "\x00"), base / UPLOADS_ENTRY)
    return manifest


def init_company(db: Path, name: str) -> str:
    sys.path.insert(0, str(ROOT))
    from core.store import Store
    Store(db)  # sukuria schemą
    con = sqlite3.connect(db)
    try:
        with con:
            if con.execute("SELECT 1 FROM companies WHERE id=1").fetchone():
                return "Įmonė Nr. 1 jau yra, nieko nekeista."
            con.execute("INSERT INTO companies(id, name) VALUES (1, ?)", (name,))
    finally:
        con.close()
    return f"Sukurta įmonė Nr. 1: {name}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.admin", description="UNO administravimas")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="Sukurti atsarginę kopiją (zip)")
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--db", type=Path, default=None)
    b.add_argument("--uploads", type=Path, default=None)
    v = sub.add_parser("verify", help="Patikrinti archyvo vientisumą")
    v.add_argument("zip", type=Path)
    r = sub.add_parser("restore", help="Atkurti iš archyvo")
    r.add_argument("zip", type=Path)
    r.add_argument("--target", required=True, type=Path)
    r.add_argument("--force", action="store_true")
    i = sub.add_parser("init", help="Sukurti įmonę Nr. 1, jei jos nėra")
    i.add_argument("--company", required=True)
    i.add_argument("--db", type=Path, default=None)
    return p


def run(args: argparse.Namespace) -> str:
    if args.cmd == "backup":
        z = make_backup(args.db or default_db(), args.out, args.uploads)
        return f"Atsarginė kopija sukurta: {z}"
    if args.cmd == "verify":
        m = verify_backup(args.zip)
        return (f"Archyvas tvarkingas ({len(m['files'])} failai, programa {m['app_version']}, "
                f"taisyklės {m['rules_version']}).")
    if args.cmd == "restore":
        m = restore_backup(args.zip, args.target, args.force)
        return f"Atkurta į {args.target} ({len(m['files'])} failai, perrašyta kelių: {m['paths_rewritten']})."
    return init_company(args.db or default_db(), args.company)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(run(args))
    except AdminError as exc:
        print(f"KLAIDA: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
