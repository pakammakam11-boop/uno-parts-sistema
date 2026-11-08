"""Netikra tools/gama_snapshot.ps1 e2e testams: nukopijuoja sintetinį fixture į -OutDir/gama_<-Month>.json.

Naudojama per UNO_GAMA_SNAPSHOT_CMD. Jokių realių duomenų. Mėnuo FAIL imituoja GAMA klaidą.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gama_snapshot_synthetic.json"


def main(argv: list[str]) -> int:
    month = argv[argv.index("-Month") + 1]
    out = Path(argv[argv.index("-OutDir") + 1])
    if month == "2026-07":
        print("Nepavyko prisijungti prie GAMA serverio\nantra eilutė", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, out / f"gama_{month}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
