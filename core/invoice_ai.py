"""Tikrų (nežinomo maketo) sąskaitų laukų ištraukimas per AI.

Naudojama tik kaip papildymas: regex parseris lieka pagrindas, AI užpildo tik tuščius laukus.
Tiekėjas parenkamas aplinkos kintamuoju UNO_AI_PROVIDER:
  cli        vietinis Claude Code CLI (`claude -p`), numatyta, kai nieko nenustatyta (Pauliaus PC);
  vertex     Google Vertex AI Gemini (REST, ES duomenų rezidencija), numatyta gamyboje (deploy/.env);
  anthropic  Anthropic Messages API (ANTHROPIC_API_KEY).
Modelis: UNO_AI_MODEL (kiekvienas tiekėjas turi savo numatytąjį). Nemokamas Gemini API (AI Studio raktas)
NENAUDOJAMAS: nemokamame lygyje užklausas Google gali naudoti produkto gerinimui, nėra DPA.
Siunčiamas tik sąskaitos PDF tekstas (iki MAX_TEXT_CHARS): jokių failų pavadinimų, siuntėjų ar laiško metaduomenų;
tekstas ir atsakymai į žurnalą nerašomi.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import hashlib
import subprocess
import threading
import time
from pathlib import Path
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from core.models import InvoiceLine

log = logging.getLogger("uno.invoice_ai")

TOLERANCE = Decimal("0.02")  # kaip core.invoices (cikliško importo išvengimui)

TIMEOUT_S = 90
AI_MODEL = "sonnet"  # CLI numatytasis: greitas ir pakankamai tikslus laukų ištraukimui
DEFAULT_MODELS = {"cli": AI_MODEL, "anthropic": "claude-sonnet-5-5", "vertex": "gemini-3.8-flash"}
PROVIDERS = tuple(DEFAULT_MODELS)
DEFAULT_LOCATION = "eu"  # gemini-3.8-flash: global, us, eu (daugiaregionis, ES apdorojimas); žr. deploy/DIEGIMAS_VPS.md
MAX_TEXT_CHARS = 12000
MAX_OUTPUT_TOKENS = 8192
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
RETRY_STATUSES = {429, 500, 502, 503, 504}
ATTEMPTS = 2
RETRY_DELAY_S = 2.0

PROMPT = """Ištrauk laukų reikšmes iš šios pirkimo sąskaitos faktūros teksto.
Atsakyk TIK vienu JSON objektu, be paaiškinimų, su raktais:
number, issue_date (YYYY-MM-DD), due_date (YYYY-MM-DD), supplier_name, supplier_company_code,
supplier_vat_code, supplier_iban, net, vat, gross (sumos kaip tekstas su tašku, pvz. "123.45", arba null),
lines: [{"description", "qty", "unit_price", "vat_rate" (procentai, pvz. 21), "net"}].
Tiekėjas yra pardavėjas, ne pirkėjas. Ko nėra tekste, rašyk null.

SĄSKAITOS TEKSTAS:
"""


def _parse_json(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    for candidate in (raw, raw[raw.find("{"):raw.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def extract_fields(text: str, cache_dir: Path | None = None) -> dict | None:
    """Kviečia `claude -p` (headless). Bet kokia klaida -> None.
    cache_dir: tas pats tekstas antrą kartą AI nekviečia (importas iš pašto kartojamas)."""
    cached = cache_dir / (hashlib.sha256(text.encode("utf-8")).hexdigest() + ".json") if cache_dir else None
    if cached and cached.exists():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data = _call_claude(text)
    if cached and data:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def provider() -> str:
    """UNO_AI_PROVIDER (cli | vertex | anthropic); nenustačius arba su klaida: cli."""
    name = os.environ.get("UNO_AI_PROVIDER", "").strip().lower()
    if name in PROVIDERS:
        return name
    if name:
        log.warning("Nežinomas UNO_AI_PROVIDER, naudojamas cli (galimi: %s)", ", ".join(PROVIDERS))
    return "cli"


def model_name(name: str) -> str:
    return os.environ.get("UNO_AI_MODEL", "").strip() or DEFAULT_MODELS[name]


def _call_claude(text: str) -> dict | None:
    """Pasirinkto tiekėjo kvietimas (vardas istorinis). Bet kokia klaida -> None, regex rezultatas lieka."""
    if not text.strip():
        return None
    name = provider()
    prompt = PROMPT + text[:MAX_TEXT_CHARS]
    try:
        raw = {"vertex": _call_vertex, "anthropic": _call_anthropic, "cli": _call_cli}[name](prompt)
    except Exception as exc:  # tinklas, kreditai, formatas: žurnale be turinio
        log.warning("AI kvietimas (%s) nepavyko: %s", name, type(exc).__name__)
        return None
    return _parse_json(raw) if raw else None


def _call_cli(prompt: str) -> str | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    proc = subprocess.run([exe, "-p", "--output-format", "json", "--model", model_name("cli")],
                          input=prompt, cwd=tempfile.gettempdir(),
                          capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S)
    if proc.returncode != 0:
        return None
    return str(json.loads(proc.stdout).get("result", ""))


def _post_json(url: str, headers: dict, body: dict) -> dict:
    """POST su vienu pakartojimu laikinoms klaidoms (429/5xx/tinklas). Be turinio žurnale."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        final = attempt + 1 >= ATTEMPTS
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_S)
        except httpx.TransportError as exc:
            if final:
                raise
            last = exc
        else:
            if resp.status_code not in RETRY_STATUSES or final:
                resp.raise_for_status()
                return resp.json()
            last = RuntimeError(f"HTTP {resp.status_code}")
        time.sleep(RETRY_DELAY_S)
    raise last or RuntimeError("AI užklausa nepavyko")


def _call_anthropic(prompt: str) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    data = _post_json(ANTHROPIC_URL,
                      {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
                      {"model": model_name("anthropic"), "max_tokens": MAX_OUTPUT_TOKENS,
                       "messages": [{"role": "user", "content": prompt}]})
    blocks = data.get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text") or None


_cred_lock = threading.Lock()
_cred_cache: dict[str, tuple[object, str]] = {}


def vertex_access_token() -> tuple[str, str]:
    """(prieigos žetonas, projektas). Raktas iš GOOGLE_SA_KEY_B64 (tas pats service account kaip Maršrutuose),
    kitaip Application Default Credentials. Žetonas talpinamas iki galiojimo pabaigos."""
    import google.auth
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SA_KEY_B64", "").strip()
    with _cred_lock:
        info = json.loads(base64.b64decode(raw)) if raw else None
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip() or (info or {}).get("project_id", "")
        cached = _cred_cache.get(raw or "adc")  # (credentials, ADC projektas): projektas talpinamas kartu
        if cached is None:
            if info:
                cached = (service_account.Credentials.from_service_account_info(info, scopes=[CLOUD_SCOPE]), "")
            else:
                creds, adc_project = google.auth.default(scopes=[CLOUD_SCOPE])
                cached = (creds, adc_project or "")
            _cred_cache[raw or "adc"] = cached
        creds, cached_project = cached
        project = project or cached_project
        if not creds.valid:
            creds.refresh(Request())
        if not project:
            raise RuntimeError("Nenustatytas GOOGLE_CLOUD_PROJECT")
        return creds.token, project


def vertex_url(project: str, location: str, model: str) -> str:
    """Daugiaregionis (us/eu): aiplatform.<loc>.rep.googleapis.com; global: aiplatform.googleapis.com;
    regioninis (pvz. europe-west1): <loc>-aiplatform.googleapis.com."""
    if location in ("us", "eu"):
        host = f"aiplatform.{location}.rep.googleapis.com"
    elif location == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{location}-aiplatform.googleapis.com"
    return (f"https://{host}/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent")


def _call_vertex(prompt: str) -> str | None:
    token, project = vertex_access_token()
    location = os.environ.get("UNO_AI_LOCATION", "").strip() or DEFAULT_LOCATION
    data = _post_json(vertex_url(project, location, model_name("vertex")),
                      {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json",
                                            "maxOutputTokens": MAX_OUTPUT_TOKENS,
                                            # laukų ištraukimui ilgas „mąstymas“ nereikalingas: be jo didesnės
                                            # sąskaitos užtrukdavo >25 s
                                            "thinkingConfig": {"thinkingLevel": "low"}}})
    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")) or None


def _dec(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _date(value) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _lines(raw) -> tuple[list[InvoiceLine], list[tuple[str, Decimal, Decimal]]]:
    """Eilutės ir neatitikimai: atspausdinta eilutės suma vs kiekis x kaina (kaip regex kelyje)."""
    out: list[InvoiceLine] = []
    mismatches: list[tuple[str, Decimal, Decimal]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            return [], []
        qty, price, rate = _dec(item.get("qty")), _dec(item.get("unit_price")), _dec(item.get("vat_rate"))
        desc = str(item.get("description") or "").strip()
        if None in (qty, price, rate) or not desc:
            return [], []  # ne visi skaičiai nuskaitomi -> eilučių nekuriam
        line = InvoiceLine(desc, qty, price, rate / 100)
        out.append(line)
        printed = _dec(item.get("net"))
        if printed is not None and abs(printed - line.net) > TOLERANCE:
            mismatches.append((desc, printed, line.net))
    return out, mismatches


def _fill(p, name: str, value, override: bool = False) -> None:
    if value not in (None, "", []) and (override or getattr(p, name) in (None, "", [])):
        setattr(p, name, value)


def enrich(p, text: str, *, prefer_ai: bool = False, cache_dir: Path | None = None):
    """Užpildo tuščius ParsedInvoice laukus. prefer_ai=True (nežinomas maketas, regex beveik nieko nerado):
    AI reikšmės perrašo regex spėjimus, nes regex tada dažnai pagauna ne tą lauką (pvz. pirkėją)."""
    data = extract_fields(text, cache_dir) if cache_dir else extract_fields(text)
    if not data:
        return p
    for key in ("number", "supplier_name", "supplier_company_code", "supplier_vat_code", "supplier_iban"):
        _fill(p, key, str(data.get(key) or "").strip(), prefer_ai)
    _fill(p, "issue_date", _date(data.get("issue_date")), prefer_ai)
    _fill(p, "due_date", _date(data.get("due_date")), prefer_ai)
    for key in ("net", "vat", "gross"):
        _fill(p, key, _dec(data.get(key)), prefer_ai)
    ai_lines, ai_mismatches = _lines(data.get("lines"))
    if ai_lines and (prefer_ai or not p.lines):
        p.lines, p.line_mismatches = ai_lines, ai_mismatches
    return p
