"""Vercel adapteris UNO Parts demo: tik išgalvoti duomenys, rašymas tik į /tmp."""
import base64
import hmac
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TMP = Path("/tmp/uno")
TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("UNO_MAIL_DIR", str(TMP / "mail" / "mail"))
os.environ.setdefault("UNO_SUPPLIERS_FILE", str(TMP / "suppliers.json"))
# Tikra UNO tiekejų kartoteka ir GAMA nuorodos (iš Rivilės, private/): kopija į /tmp, kad būtų galima pildyti
for _name in ("suppliers.json", "gama_purchase_refs.json"):
    _src, _dst = ROOT / "private" / _name, TMP / _name
    if _src.is_file() and not _dst.exists():
        shutil.copyfile(_src, _dst)
os.environ.setdefault("UNO_DB", str(TMP / "uno.db"))
os.environ.setdefault("UNO_MAIL_AI", "0")
os.environ.setdefault("UNO_GAMA_DATA_DIR", str(TMP / "gama"))
os.environ.setdefault("HR_DEMO", "1")          # demo personos ir reset
os.environ.setdefault("HR_SIGNING", "demo")     # Dokobit imitacija
# HR ir dashboard DB: Neon DATABASE_URL (Vercel Marketplace); Secure slapukas per VERCEL=1

from core import invoices as _inv  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402


def _register_fonts() -> None:
    if _inv.FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(_inv.FONT, str(ROOT / "fonts" / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_inv.FONT_BOLD, str(ROOT / "fonts" / "DejaVuSans-Bold.ttf")))


_inv.register_fonts = _register_fonts

from app import services  # noqa: E402
from core import ai_bridge  # noqa: E402

services.OUT = TMP / "out"
ai_bridge.VAULT = TMP / "vault"

from app.main import app  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402

from sqlalchemy import text as _sql  # noqa: E402

from core import invoice_ai, invoices as _invoices  # noqa: E402
from core.hr import db as _db  # noqa: E402

_log = logging.getLogger("uno.vercel")
_invoices.AI_WORKERS = 12          # AI kvietimai laukia tinklo, ne CPU
_extract_uncached = invoice_ai.extract_fields
_cache_ready = False


def _ensure_cache_table(conn) -> None:
    global _cache_ready
    if _cache_ready:
        return
    conn.execute(_sql("CREATE TABLE IF NOT EXISTS uno_ai_cache (key text PRIMARY KEY, data text NOT NULL, "
                      "created timestamptz NOT NULL DEFAULT now())"))
    conn.execute(_sql("ALTER TABLE uno_ai_cache ENABLE ROW LEVEL SECURITY"))  # tik serverio savininkas
    _cache_ready = True


def _extract_db_cached(text: str, cache_dir=None):
    """AI laukų talpykla Neon DB: /tmp dingsta tarp Vercel egzempliorių, todėl kartotinis importas būtų lėtas."""
    if not os.environ.get("DATABASE_URL"):
        return _extract_uncached(text, cache_dir)
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        with _db.get_engine().begin() as conn:
            _ensure_cache_table(conn)
            row = conn.execute(_sql("SELECT data FROM uno_ai_cache WHERE key = :k AND (data <> 'null' "
                                   "OR created > now() - interval '1 hour')"), {"k": key}).first()
        if row:
            return json.loads(row[0])   # "null" = AI anksčiau neatsakė: nebandom kas kartą iš naujo
    except Exception:
        _log.exception("AI talpyklos skaitymas nepavyko")
    data = _extract_uncached(text, None)
    try:
        with _db.get_engine().begin() as conn:
            conn.execute(_sql("INSERT INTO uno_ai_cache (key, data) VALUES (:k, :d) ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, created = now()"),
                         {"k": key, "d": json.dumps(data or None, ensure_ascii=False)})
    except Exception:
        _log.exception("AI talpyklos įrašymas nepavyko")
    return data


invoice_ai.extract_fields = _extract_db_cached


from starlette.responses import Response  # noqa: E402

from app import main as _main  # noqa: E402


@app.middleware("http")
async def regenerate_downloads(request, call_next):
    """Kitas serverio egzempliorius gali neturėti /tmp failų: atsisiuntimui sugeneruojam iš naujo."""
    path = request.url.path
    if path.startswith("/api/download/"):
        name = path[len("/api/download/"):]
        if not (services.OUT / name).is_file() and not _main.RESULTS.get("invoices", {}).get("_source"):
            for run in (services.run_invoices, services.run_bank, services.run_payroll):
                try:
                    run()
                except Exception:
                    pass
    return await call_next(request)


SITE_USER = "uno"


def _site_password() -> str:
    return os.environ.get("UNO_SITE_PASSWORD", "").strip()


@app.middleware("http")
async def site_password(request, call_next):
    """Visa svetainė už slaptažodžio (HTTP Basic), nes čia tikros UNO sąskaitos iš pašto."""
    if request.url.path.startswith("/api/export/"):   # Rivilės VM: savas raktas X-UNO-Token (app/export_api.py)
        return await call_next(request)
    password = _site_password()
    if not password:
        return Response("Svetaine uzrakinta: nenustatytas UNO_SITE_PASSWORD", status_code=503)
    header = request.headers.get("authorization", "")
    ok = False
    if header.lower().startswith("basic "):
        try:
            user, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            ok = hmac.compare_digest(user, SITE_USER) and hmac.compare_digest(given.encode(), password.encode())
        except (ValueError, UnicodeDecodeError):
            ok = False
    if not ok:
        return Response("Reikia prisijungti", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="UNO Parts", charset="UTF-8"'})
    return await call_next(request)


# --- laiko matavimas (diagnostika): kiek trunka paštas ir apdorojimas
import time as _time  # noqa: E402

from core import mail_import as _mi, pdf_safe as _ps  # noqa: E402


def _timed(mod, name):
    orig = getattr(mod, name)

    def wrapper(*a, **kw):
        t = _time.monotonic()
        try:
            return orig(*a, **kw)
        finally:
            print(f"UNO_TIMING {name} {(_time.monotonic() - t):.2f}s", flush=True)
    setattr(mod, name, wrapper)


_timed(_mi, "fetch_invoice_pdfs")
_timed(services, "_invoices_result")
_timed(_ps, "extract_text")
_timed(invoice_ai, "_call_claude")

# Vercel: naršyklės nėra; Agnum nuorodos parsiunčiamos per jų HTTP API (core.link_invoices.fetch_agnum_api).
os.environ.setdefault("UNO_LINK_BROWSER", "0")
invoice_ai.TIMEOUT_S = 60          # vienas pakibęs AI kvietimas neturi laikyti viso importo
