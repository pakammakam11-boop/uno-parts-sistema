"""CLI: py -3.12 -m tools.gama_check <snapshot.json> [--month YYYY-MM] [--out <dir>]

Skaito GAMA snapshot'ą, tikrina mėnesio mokesčių išskaitymus ir rašo gama_check_<mėnuo>.xlsx
(lapai Suvestinė ir Radiniai). Numatytasis katalogas: snapshot'o katalogas, ne repo.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from openpyxl import Workbook

from core.gama import load_snapshot
from core.gama_check import COMPONENTS, check_month


def write_report(path: Path, findings, summary: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Suvestinė"
    ws.append(["Rodiklis", "Reikšmė", "Neatitikimai"])
    ws.append(["Mėnuo", summary["month"], ""])
    ws.append(["Darbuotojų", summary["employees"], ""])
    ws.append(["Metinė 36 VDU patikra", summary.get("annual_check", ""), ""])
    for comp in COMPONENTS:
        c = summary["components"][comp]
        ws.append([f"{comp} tvarkoje", c["ok"], c["mismatch"]])
    for mode, n in summary["npd_modes"].items():
        ws.append([f"NPD režimas {mode}", n, ""])
    for sev, n in summary["findings"].items():
        ws.append([f"Radiniai {sev}", n, ""])

    rs = wb.create_sheet("Radiniai")
    rs.append(["Svarba", "Darbuotojas (TB)", "Komponentas", "Pavadinimas", "Tikėtina", "GAMA", "Skirtumas", "Aprašymas"])
    for f in findings:
        e = f.extra
        rs.append([f.severity, f.ref, e.get("component", ""), f.title,
                   e.get("expected", ""), e.get("actual", ""), e.get("diff", ""), f.detail])
    wb.save(path)


def repo_root() -> Path | None:
    """Artimiausias katalogas su .git, einant aukštyn nuo šio failo."""
    for d in Path(__file__).resolve().parents:
        if (d / ".git").exists():
            return d
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="gama_check")
    p.add_argument("snapshot")
    p.add_argument("--month")
    p.add_argument("--out")
    p.add_argument("--allow-repo-output", action="store_true",
                   help="leisti rašyti ataskaitą repo viduje (pagal nutylėjimą draudžiama: asmens duomenys)")
    args = p.parse_args(argv)

    src = Path(args.snapshot)
    snapshot = load_snapshot(src)
    month = args.month or snapshot.month
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        print(f"Klaida: netaisyklingas mėnuo '{month}', tikimasi YYYY-MM", file=sys.stderr)
        return 2
    if month not in snapshot.months():
        print(f"Klaida: mėnesio {month} snapshot'e nėra (yra: {', '.join(snapshot.months()) or 'nieko'})",
              file=sys.stderr)
        return 2
    out_dir = (Path(args.out) if args.out else src.resolve().parent).resolve()
    root = repo_root()
    if root and not args.allow_repo_output and (out_dir == root or root in out_dir.parents):
        print(f"Klaida: išvesties katalogas {out_dir} yra repo viduje. Nurodykite --out už repo "
              "arba --allow-repo-output.", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    findings, summary = check_month(snapshot, month)
    out = out_dir / f"gama_check_{month}.xlsx"
    write_report(out, findings, summary)

    print(f"Mėnuo {month}, darbuotojų {summary['employees']}")
    for comp in COMPONENTS:
        c = summary["components"][comp]
        print(f"  {comp:9} ok {c['ok']:3}  mismatch {c['mismatch']:3}")
    print("  NPD režimai:", summary["npd_modes"])
    print("  Radiniai:", ", ".join(f"{k} {v}" for k, v in summary["findings"].items()))
    print(f"Ataskaita: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
