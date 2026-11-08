"""Sąskaitos, siunčiamos tik kaip nuoroda (pvz. Agnum): nuorodų paieška laiške ir PDF atsisiuntimas.

Saugumas (žr. core/link_safety.py): URL kanonizuojamas vieną kartą ir tas pats perduodamas httpx bei naršyklei;
visas išėjimas eina per vietinį CONNECT tarpinį serverį (tik leidžiami domenai, tik viešieji IP, tik 443);
kiekvienas bandymas vyksta atskirame procese su 60 s watchdog; priimamas tik PDF (%PDF-, iki 20 MB, dekoduotų
baitų riba). Žurnale tik domenas + trumpas URL maišos identifikatorius.
"""
from __future__ import annotations

import codecs
import base64
import html
import json
import logging
import multiprocessing
import os
import re
import shutil
import tempfile
import threading
import zlib
import time
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import psutil

from core import link_safety
from core.link_safety import canonicalize, link_label

log = logging.getLogger("uno.link_invoices")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO žurnale rašo pilną URL su žetonu

DEFAULT_DOMAINS = "agnum.lt"
MAX_LINKS_PER_MAIL = 3
LINK_TIMEOUT_S = 60                      # griežtas vieno bandymo terminas (watchdog)
CHILD_SOFT_DEADLINE_S = 55               # vidinis terminas, kad vaikas spėtų tvarkingai užsidaryti
MAIL_BUDGET_S = 150                      # visų vieno laiško nuorodų laikas
MAIL_BUDGET_BYTES = 100 * 1024 * 1024    # visų vieno laiško nuorodų srautas
ATTEMPT_BYTES = 40 * 1024 * 1024         # vieno bandymo srautas per tarpinį serverį (puslapis + PDF)
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_TMP_BYTES = 100 * 1024 * 1024        # vaiko laikino katalogo riba (tikrina tėvas)
ALLOWED_ENCODINGS = {"identity", "gzip", "x-gzip", "deflate"}
BUTTON_RE = re.compile(r"Parsisi|Atsisi|Download", re.I)
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
NEEDS_BROWSER = "reikia naršyklės (Playwright Chromium nėra įdiegtas)"
BROWSER_ARGS = [
    "--proxy-bypass-list=<-loopback>",                      # net loopback eina per tarpinį serverį
    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",  # jokio tiesioginio DNS
    "--disable-quic", "--disable-dev-shm-usage", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--no-first-run",
    "--disk-cache-size=1", "--media-cache-size=1",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
]
_HREF_RE = re.compile(r"""href\s*=\s*["']?(https?://[^"'\s<>]+)""", re.I)
_URL_RE = re.compile(r"""https?://[^\s<>"')\]]+""", re.I)


def parse_domains(raw: str | None) -> list[str]:
    value = DEFAULT_DOMAINS if raw is None else raw
    return [d.strip().lower().lstrip(".") for d in value.split(",") if d.strip()]


def is_allowed(url: str, domains: list[str]) -> bool:
    return canonicalize(url, domains) is not None


@dataclass
class LinkBudget:
    """Vieno laiško visų nuorodų laiko ir srauto biudžetas."""
    deadline: float = field(default_factory=lambda: time.monotonic() + MAIL_BUDGET_S)
    bytes_left: int = MAIL_BUDGET_BYTES

    def time_left(self) -> float:
        return self.deadline - time.monotonic()


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        codecs.lookup(charset)
    except (LookupError, ValueError):
        charset = "utf-8"
    return payload.decode(charset, "replace")


def extract_links(msg: Message) -> list[str]:
    """http(s) nuorodos iš text/html ir text/plain dalių (be priedų), be pasikartojimų, laiško tvarka."""
    found: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        pattern = _HREF_RE if ctype == "text/html" else _URL_RE
        for raw in pattern.findall(_text_of(part)):
            url = html.unescape(raw).rstrip(".,;")
            if url not in found:
                found.append(url)
    return found


def _is_pdf(data: bytes | None) -> bool:
    return bool(data) and len(data) <= MAX_PDF_BYTES and data[:5] == b"%PDF-"


# ---------- vaiko proceso darbas (tik čia vyksta tinklas) ----------

class _CookieStore:
    """Griežta sausainių saugykla (httpx savo jar nenaudojamas): be Domain atributo sausainis galioja tik tam pačiam hostui,
    su Domain jis ir subdomenams; kelias pagal ribą; tik https. Atnaujinama iš kiekvieno atsakymo Set-Cookie."""

    def __init__(self, seed: list[dict] | None = None) -> None:
        self._items: dict[tuple, dict] = {}
        for c in seed or []:  # Playwright: domenas su pradiniu tašku = domeno sausainis, be taško = tik hostas
            raw = str(c.get("domain", "")).lower()
            if raw:
                self._put(c["name"], c["value"], raw.lstrip("."), raw.startswith("."), str(c.get("path") or "/"))

    def _put(self, name: str, value: str, domain: str, domain_wide: bool, path: str) -> None:
        self._items[(name, domain, domain_wide, path)] = {"name": name, "value": value, "domain": domain,
                                                          "wide": domain_wide, "path": path}

    def header(self, url: str) -> str:
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path or "/"
        if parts.scheme != "https":
            return ""
        matched = []
        for c in self._items.values():
            host_ok = host == c["domain"] or (c["wide"] and host.endswith("." + c["domain"]))
            cpath = c["path"]
            path_ok = path == cpath or path.startswith(cpath if cpath.endswith("/") else cpath + "/")
            if host_ok and path_ok:
                matched.append(c)
        matched.sort(key=lambda c: -len(c["path"]))
        return "; ".join(f"{c['name']}={c['value']}" for c in matched)

    def update(self, url: str, set_cookie_headers: list[str]) -> None:
        from http.cookies import CookieError, SimpleCookie

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        default_path = (parts.path or "/").rsplit("/", 1)[0] or "/"
        for header in set_cookie_headers:
            jar = SimpleCookie()
            try:
                jar.load(header)
            except CookieError:
                continue
            for name, morsel in jar.items():
                domain_attr = morsel["domain"].lower().lstrip(".")
                if domain_attr and not (host == domain_attr or host.endswith("." + domain_attr)):
                    continue  # svetimas Domain atmetamas
                domain, wide = (domain_attr, True) if domain_attr else (host, False)
                path = morsel["path"] or default_path
                if morsel["max-age"] in ("0",) or morsel["max-age"].startswith("-"):
                    self._items.pop((name, domain, wide, path), None)  # ištrinimas
                else:
                    self._put(name, morsel.value, domain, wide, path)


def _cookies_for(cookies: list[dict] | None, url: str) -> str:
    return _CookieStore(cookies).header(url)


def _fetch_http(url: str, domains: list[str], deadline: float, proxy_url: str | None = None,
                cookies: list[dict] | None = None) -> bytes | None:
    """GET per tarpinį serverį; peradresavimai sekami ranka (kiekvienas kanonizuojamas); riboto dydžio skaitymas."""
    import httpx

    current = canonicalize(url, domains)
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    store = _CookieStore(cookies)
    with httpx.Client(headers=headers, follow_redirects=False, proxy=proxy_url, trust_env=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            client.cookies.clear()  # httpx vidinis jar išjungtas: sausainius renka tik _CookieStore
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not current:
                return None
            hop_headers = {"Cookie": cookie} if (cookie := store.header(current)) else None
            with client.stream("GET", current, headers=hop_headers, timeout=min(remaining, 30)) as resp:  # sausainiai kiekvienam hop
                store.update(current, resp.headers.get_list("set-cookie"))
                if resp.is_redirect and resp.headers.get("location"):
                    current = canonicalize(urljoin(current, resp.headers["location"]), domains)
                    continue
                if resp.status_code != 200:
                    return None
                return _read_pdf_stream(resp, deadline)
    return None


def _encodings(header: str) -> list[str] | None:
    """Content-Encoding sąrašas be identity; None, jei kodavimas nepalaikomas arba sukrautas (>1)."""
    names = [e.strip().lower() for e in header.split(",") if e.strip()]
    if any(n not in ALLOWED_ENCODINGS for n in names):
        return None
    names = [n for n in names if n != "identity"]
    return names if len(names) <= 1 else None


def _decoded_chunks(raw_chunks, encoding: str | None, limit: int | None = None):
    """Žali baitai -> dekoduoti baitai su griežta bendra išvesties riba; meta ValueError viršijus ar sugadinus."""
    limit = MAX_PDF_BYTES if limit is None else limit
    if encoding is None:
        yield from raw_chunks
        return
    wbits = 16 + zlib.MAX_WBITS if "gzip" in encoding else zlib.MAX_WBITS
    decoder = zlib.decompressobj(wbits)
    produced, first = 0, True
    for chunk in raw_chunks:
        pending = chunk
        while pending:
            try:
                out = decoder.decompress(pending, limit + 1 - produced)
            except zlib.error:
                if not (first and encoding == "deflate"):
                    raise ValueError("blogas suspaudimas") from None
                decoder = zlib.decompressobj(-zlib.MAX_WBITS)  # deflate be zlib antraštės
                out = decoder.decompress(pending, limit + 1 - produced)
            first = False
            produced += len(out)
            if produced > limit:
                raise ValueError("per didelis")
            if out:
                yield out
            pending = decoder.unconsumed_tail


def _read_body(resp, deadline: float, limit: int, pdf: bool = False) -> bytearray | None:
    """Atsakymo kūnas srautu: žali IR dekoduoti baitai riboti `limit`, terminas tikrinamas po kiekvieno tinklo skaitymo
    (be kaupimo į didelius gabalus); None viršijus ribą, terminą, esant nepalaikomam kodavimui ar blogam turiniui."""
    encodings = _encodings(resp.headers.get("content-encoding", ""))
    if encodings is None:
        return None
    raw_total = 0

    def raw_chunks():
        nonlocal raw_total
        for chunk in resp.iter_raw():  # be chunk_size: grąžina tai, ką atidavė tinklas
            raw_total += len(chunk)
            if raw_total > limit + 1024 or time.monotonic() > deadline:
                raise ValueError("riba")
            yield chunk

    buf = bytearray()  # vienas ribojamas buferis (be mažų fragmentų sąrašo ir join)
    prefix_checked = False
    try:
        for chunk in _decoded_chunks(raw_chunks(), encodings[0] if encodings else None, limit):
            buf += chunk
            if len(buf) > limit or time.monotonic() > deadline:
                return None
            if pdf and not prefix_checked and len(buf) >= 5:
                if buf[:5] != b"%PDF-":
                    return None
                prefix_checked = True
    except ValueError:
        return None
    return buf


def _read_pdf_stream(resp, deadline: float) -> bytes | None:
    data = _read_body(resp, deadline, MAX_PDF_BYTES, pdf=True)
    return data if _is_pdf(data) else None


def _minimal_browser_env(tmp: str) -> dict[str, str]:
    env = {"HOME": tmp, "TMPDIR": tmp, "TMP": tmp, "TEMP": tmp, "PATH": os.environ.get("PATH", "")}
    for key in ("SYSTEMROOT", "SystemDrive", "WINDIR", "LANG", "PLAYWRIGHT_BROWSERS_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _launch(pw, proxy_url: str, tmp: str):
    base = dict(proxy={"server": proxy_url}, args=BROWSER_ARGS, env=_minimal_browser_env(tmp),
                downloads_path=str(Path(tmp) / "dl"), timeout=20000)
    try:
        return pw.chromium.launch(chromium_sandbox=True, **base)
    except Exception:
        if os.environ.get("UNO_BROWSER_NO_SANDBOX") != "1":
            raise
        log.warning("Chromium smėlio dėžė (sandbox) išjungta per UNO_BROWSER_NO_SANDBOX=1")
        return pw.chromium.launch(chromium_sandbox=False, **base)


def _block_non_https(route) -> None:
    if route.request.url.startswith(("https://", "data:", "blob:", "about:")):
        route.continue_()
    else:
        route.abort()


# Blob kabliukas: native metodai išsaugomi init skripte (puslapis jų nebepakeis), saugoma tik paskutiniai 20 Blob.
BLOB_HOOK = """
(() => {
  const sizeGet = Object.getOwnPropertyDescriptor(Blob.prototype, 'size').get;
  const sliceFn = Blob.prototype.slice, bufFn = Blob.prototype.arrayBuffer;
  const origCreate = URL.createObjectURL, store = [];
  URL.createObjectURL = function (b) {
    try { sizeGet.call(b); store.push(b); if (store.length > 20) store.shift(); } catch (e) {}
    return origCreate.call(URL, b);
  };
  const reader = async (limit) => {
    for (const b of store.slice().reverse()) {
      const size = sizeGet.call(b);
      if (size > limit || size < 5) continue;
      const head = new Uint8Array(await bufFn.call(sliceFn.call(b, 0, 5)));
      if (String.fromCharCode(...head) !== '%PDF-') continue;
      const buf = new Uint8Array(await bufFn.call(b));
      if (buf.length > limit) continue;
      let out = ''; for (let i = 0; i < buf.length; i += 32768) out += String.fromCharCode(...buf.subarray(i, i + 32768));
      return btoa(out);
    }
    return null;
  };
  Object.defineProperty(window, '__unoReadBlob', {value: reader, writable: false, configurable: false});
})();
"""
BLOB_READ = "(limit) => window.__unoReadBlob ? window.__unoReadBlob(limit) : null"


def _browser_cookies(ctx) -> list[dict]:
    try:
        return list(ctx.cookies())
    except Exception:
        return []


def _fetch_browser(url: str, domains: list[str], deadline: float, proxy_url: str, tmp: str) -> tuple[bytes | None, str]:
    """Naršyklė tik surenka nuorodą į PDF (atsisiuntimai išjungti); pačius baitus atsiunčia riboto dydžio skaitytuvas."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, NEEDS_BROWSER
    candidates: list[str] = []

    def note_response(resp) -> None:
        try:
            kind = resp.headers.get("content-type", "") + resp.headers.get("content-disposition", "")
            if resp.request.method == "GET" and any(t in kind.lower() for t in ("pdf", "octet-stream", "attachment")):
                canonical = canonicalize(resp.url, domains)
                if canonical and canonical not in candidates:
                    candidates.append(canonical)
        except Exception:
            pass

    def note_download(download) -> None:
        canonical = canonicalize(download.url, domains)  # blob:/data: atmetami; jie skaitomi per _read_blob
        if canonical and canonical not in candidates:
            candidates.append(canonical)

    def watch_page(page) -> None:
        page.on("response", note_response)
        page.on("download", note_download)

    try:
        with sync_playwright() as pw:
            try:
                browser = _launch(pw, proxy_url, tmp)
            except Exception:
                return None, NEEDS_BROWSER
            try:
                ctx = browser.new_context(accept_downloads=False, user_agent=USER_AGENT, service_workers="block")
                ctx.route("**/*", _block_non_https)
                ctx.add_init_script(BLOB_HOOK)
                ctx.on("page", watch_page)
                page = ctx.new_page()
                watch_page(page)
                page.goto(url, wait_until="networkidle", timeout=max(1000, int((deadline - time.monotonic()) * 1000)))
                if page.url != "about:blank" and not canonicalize(page.url, domains):
                    return None, "nuoroda nukreipė į neleistiną domeną"
                data = _click_and_collect(page, ctx, candidates, domains, deadline, proxy_url)
                return (data, "") if data else (None, "PDF negautas")
            finally:
                browser.close()
    except Exception as exc:  # Playwright laiko / naršyklės klaidos: be nuorodos žetono
        return None, f"naršyklės klaida ({type(exc).__name__})"


def _click_and_collect(page, ctx, candidates: list[str], domains: list[str], deadline: float, proxy_url: str) -> bytes | None:
    button = page.get_by_role("button", name=BUTTON_RE)
    if not button.count():
        button = page.get_by_text(BUTTON_RE)
    if not button.count():
        button = page.locator("button")
    if not button.count():
        return None
    button.first.click(timeout=15000)
    tried = 0
    while time.monotonic() < deadline:
        for candidate in candidates[tried:]:
            tried += 1
            data = _fetch_http(candidate, domains, deadline, proxy_url, _browser_cookies(ctx))
            if data:
                return data
        for open_page in list(ctx.pages):  # ir iškylantys langai
            data = _read_blob(open_page)
            if data:
                return data
        page.wait_for_timeout(500)
    return None


def _read_blob(page) -> bytes | None:
    """blob: PDF: dydis tikrinamas JS viduje prieš skaitant turinį."""
    try:
        encoded = page.evaluate(BLOB_READ, MAX_PDF_BYTES)
        if not isinstance(encoded, str) or len(encoded) > MAX_PDF_BYTES * 4 // 3 + 8:
            return None
        data = base64.b64decode(encoded)
    except Exception:
        return None
    return data if _is_pdf(data) else None


def _attempt(url: str, domains: list[str], tmp: str, budget_bytes: int, soft_s: float) -> tuple[bytes | None, str, int]:
    """Vienas bandymas (kviečiamas vaiko procese): HTTP, tada naršyklė; abu tik per tarpinį serverį."""
    deadline = time.monotonic() + soft_s
    with link_safety.EgressProxy(domains, max_bytes=budget_bytes) as proxy:
        used = lambda: budget_bytes - proxy.bytes_left  # noqa: E731
        try:
            data = _fetch_http(url, domains, deadline, proxy.url)
        except Exception as exc:
            log.info("GET nepavyko %s (%s)", link_label(url), type(exc).__name__)
            data = None
        if data:
            return data, "", used()
        if proxy.exceeded:
            return None, "per didelis atsakymas", used()
        data, reason = _fetch_browser(url, domains, deadline, proxy.url, tmp)
        return data, ("per didelis atsakymas" if not data and proxy.exceeded else reason), used()


def _snapshot_descendants(tmp: str) -> list[list[float]]:
    me = psutil.Process()
    pairs = []
    for child in me.children(recursive=True):
        try:
            pairs.append([child.pid, child.create_time()])
        except psutil.Error:
            pass
    target = Path(tmp) / "pids.json"
    scratch = Path(tmp) / "pids.json.new"
    scratch.write_text(json.dumps(pairs))
    os.replace(scratch, target)
    return pairs


def _start_pid_recorder(tmp: str, interval: float = 0.5) -> None:
    """Vaiko gija: nuolat įrašo visų palikuonių (net atsiskyrusių grupių) PID, kad watchdog galėtų juos rasti."""
    def run() -> None:
        while True:
            try:
                _snapshot_descendants(tmp)
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=run, daemon=True).start()


def _child_main(url: str, domains: list[str], tmp: str, budget_bytes: int, soft_s: float, conn) -> None:
    for key in ("TMPDIR", "TMP", "TEMP"):
        os.environ[key] = tmp
    _start_pid_recorder(tmp)
    try:
        data, reason, used = _attempt(url, domains, tmp, budget_bytes, soft_s)
        if data:
            (Path(tmp) / "out.pdf").write_bytes(data)
        conn.send((reason, used))
    except Exception as exc:
        conn.send((f"nuorodos apdoroti nepavyko ({type(exc).__name__})", 0))
    finally:
        conn.close()


# ---------- tėvo pusė: procesas + watchdog ----------

def _recorded_pids(tmp: str) -> list[psutil.Process]:
    try:
        pairs = json.loads((Path(tmp) / "pids.json").read_text())
    except (OSError, ValueError):
        return []
    found = []
    for pid, created in pairs:
        try:
            proc = psutil.Process(int(pid))
            if abs(proc.create_time() - created) < 1:  # tas pats procesas, ne perpanaudotas PID
                found.append(proc)
        except (psutil.Error, ValueError, TypeError):
            pass
    return found


def _kill_tree(proc, tmp: str = "") -> None:
    """Nužudo vaiką ir VISUS palikuonis (įskaitant atsiskyrusias procesų grupes ir našlaičius iš įrašų)."""
    victims: dict[int, psutil.Process] = {}
    try:
        root = psutil.Process(proc.pid)
        victims.update({p.pid: p for p in root.children(recursive=True)})
        victims[root.pid] = root
    except psutil.Error:
        pass
    if tmp:
        victims.update({p.pid: p for p in _recorded_pids(tmp)})
    for victim in victims.values():
        try:
            victim.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(list(victims.values()), timeout=5)
    try:
        proc.kill()
    except Exception:
        pass
    proc.join(5)


def _tree_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _wait_child(proc, tmp: str, allowed_s: float) -> str:
    """Laukia vaiko iki allowed_s, kas sekundę tikrindama laikino katalogo dydį. '' | 'time' | 'disk'."""
    end = time.monotonic() + allowed_s
    while proc.is_alive():
        if time.monotonic() >= end:
            return "time"
        if _tree_size(tmp) > MAX_TMP_BYTES:
            return "disk"
        proc.join(min(1.0, max(0.05, end - time.monotonic())))
    return ""


SIDECAR_TIMEOUT_S = 75


ERROR_BODY_LIMIT = 4096


def _fetch_via_sidecar(canonical: str, base_url: str, token: str, seconds: float = LINK_TIMEOUT_S,
                       max_bytes: int = ATTEMPT_BYTES) -> tuple[bytes | None, str, int]:
    """Gamyboje (LINKFETCH_URL): naršyklė veikia atskirame konteineryje. Grąžina (PDF, priežastis, sunaudoti baitai).
    Visai užklausai (antraštės + kūnas) galioja vienas absoliutus terminas; klaidų kūnas skaitomas tuo pačiu riboto
    dydžio skaitytuvu (4 KB)."""
    import httpx

    deadline = time.monotonic() + min(SIDECAR_TIMEOUT_S, seconds + 8)
    remaining = max(0.5, deadline - time.monotonic())
    # Antraštės gali ateiti tik sidecar baigus darbą: skaitymo laukimas = likęs bandymo laikas; absoliutų terminą
    # užtikrina watchdog ir terminas po kiekvieno gabalo.
    client = httpx.Client(trust_env=False, timeout=httpx.Timeout(connect=min(5.0, remaining), read=remaining,
                                                                 write=min(5.0, remaining), pool=min(5.0, remaining)))
    watchdog = threading.Timer(remaining, client.close)  # nutraukia ir užstrigusį ryšį
    watchdog.daemon = True
    watchdog.start()
    reserved = int(max_bytes)
    known: int | None = None  # sidecar pranešti sunaudoti baitai, jei jau žinomi
    try:
        with client.stream("POST", base_url.rstrip("/") + "/fetch", headers={"Authorization": f"Bearer {token}"},
                           json={"url": canonical, "max_seconds": int(seconds), "max_bytes": reserved}) as resp:
            if resp.status_code == 200 and "pdf" in resp.headers.get("content-type", ""):
                known = _as_int(resp.headers["x-bytes-used"]) if "x-bytes-used" in resp.headers else None
                data = _read_pdf_stream(resp, deadline)
                if data:
                    return data, "", known if known is not None else len(data)
                return None, "PDF negautas", known if known is not None else reserved
            reason, used = _sidecar_reason(resp, deadline)
            return None, reason, used if used is not None else reserved
    except (httpx.ConnectError, httpx.ConnectTimeout):
        log.info("Sidecar nepasiekiamas %s", link_label(canonical))
        return None, "nuorodų paslauga nepasiekiama (ConnectError)", 0  # užklausa nepasiekė sidecar: baitų nesunaudota
    except Exception as exc:  # be URL: httpx klaidų tekstuose gali būti adresas
        log.info("Sidecar atsakymas nutrūko (%s) %s", type(exc).__name__, link_label(canonical))
        # nutrūkus atsakymui: žinomas sunaudojimas, kitaip konservatyviai visas rezervuotas bandymo biudžetas
        return None, f"nuorodų paslauga nepasiekiama ({type(exc).__name__})", known if known is not None else reserved
    finally:
        watchdog.cancel()
        client.close()


def _as_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _sidecar_reason(resp, deadline: float) -> tuple[str, int | None]:
    """(priežastis, sunaudoti baitai arba None, jei sidecar jų nepranešė)."""
    body = _read_body(resp, deadline, ERROR_BODY_LIMIT)
    reason, used = "", None
    try:
        payload = json.loads(bytes(body or b"").decode("utf-8", "replace"))
        reason = str(payload.get("error", ""))[:120]
        used = _as_int(payload["bytes_used"]) if "bytes_used" in payload else None
    except (ValueError, AttributeError):
        pass
    return reason or f"nuorodų paslauga atsakė {resp.status_code}", used


# ---------------------------------------------------------------- Agnum be naršyklės
# Agnum sąskaitų puslapis (Vaadin/Hilla) PDF gauna dviem HTTP kvietimais: GetInvoiceFiles -> api/getfile/<guid>.
# Tai veikia ir be naršyklės (Vercel). Visas srautas eina per EgressProxy (tik leidžiami domenai ir viešieji IP).
_AGNUM_PATH_RE = re.compile(r"^/agnum-saskaitos/public/saskaita/([A-Za-z0-9_-]{8,128})/?$")
_AGNUM_GUID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")
AGNUM_JSON_MAX = 64 * 1024
AGNUM_TIMEOUT_S = 30


def _agnum_token(canonical: str) -> str | None:
    parts = urlsplit(canonical)
    if not (parts.hostname or "").endswith("agnum.lt"):
        return None
    match = _AGNUM_PATH_RE.match(parts.path)
    return match.group(1) if match else None


def _agnum_guids(client, base: str, token: str, deadline: float) -> list[str]:
    page = client.get(base + "public/saskaita/" + token, timeout=max(1, min(20, deadline - time.monotonic())))
    if page.status_code != 200:
        return []
    headers = {"Content-Type": "application/json", "X-CSRF-Token": client.cookies.get("csrfToken") or ""}
    with client.stream("POST", base + "connect/ClientEndpoint/GetInvoiceFiles", json={"saskLink": token},
                       headers=headers, timeout=max(1, min(20, deadline - time.monotonic()))) as resp:
        if resp.status_code != 200:
            return []
        body = _read_body(resp, deadline, AGNUM_JSON_MAX)
    try:
        files = json.loads(bytes(body or b"").decode("utf-8"))
    except ValueError:
        return []
    guids = [f.get("linkGuid", "") for f in files if isinstance(f, dict)] if isinstance(files, list) else []
    return [g for g in guids if _AGNUM_GUID_RE.match(g)][:MAX_LINKS_PER_MAIL]


def fetch_agnum_api(canonical: str, domains: list[str]) -> bytes | None:
    """Agnum PDF be naršyklės; None, jei nuoroda ne Agnum arba nepavyko (tada bandoma įprastu keliu)."""
    import httpx

    token = _agnum_token(canonical)
    if not token:
        return None
    parts = urlsplit(canonical)
    base = f"https://{parts.hostname}/agnum-saskaitos/"
    deadline = time.monotonic() + AGNUM_TIMEOUT_S
    try:
        with link_safety.EgressProxy(domains, max_bytes=ATTEMPT_BYTES) as proxy:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False, proxy=proxy.url,
                              trust_env=False) as client:
                guids = _agnum_guids(client, base, token, deadline)
                cookies = [{"name": k, "value": v, "domain": parts.hostname, "path": "/"} for k, v in client.cookies.items()]
            for guid in guids:
                data = _fetch_http(base + "api/getfile/" + guid, domains, deadline, proxy.url, cookies)
                if data and _is_pdf(data):
                    return bytes(data)
    except Exception as exc:  # be žetono žurnale
        log.info("Agnum API nepavyko %s: %s", link_label(canonical), type(exc).__name__)
    return None


def fetch_link_pdf(url: str, domains: list[str], budget: LinkBudget | None = None, *,
                   _target=_child_main) -> tuple[bytes | None, str]:
    """(PDF baitai, '') arba (None, priežastis lietuviškai). Tėvas nelaukia ilgiau nei ~LINK_TIMEOUT_S."""
    canonical = canonicalize(url, domains)
    if not canonical:
        return None, "nuoroda neleistina"
    budget = budget or LinkBudget()
    if _agnum_token(canonical):
        data = fetch_agnum_api(canonical, domains)
        if data:
            budget.bytes_left -= len(data)
            return data, ""
    if os.environ.get("UNO_LINK_BROWSER", "1") == "0":   # pvz. Vercel: naršyklės nėra
        return None, "Agnum sąskaitos parsisiųsti nepavyko" if _agnum_token(canonical) else NEEDS_BROWSER
    sidecar = os.environ.get("LINKFETCH_URL", "").strip()
    if sidecar:
        if budget.time_left() < 5 or budget.bytes_left <= 0:
            return None, "viršytas laiško nuorodų biudžetas"
        seconds = min(LINK_TIMEOUT_S, budget.time_left())  # sidecar gauna LIKUSĮ laiško biudžetą
        data, reason, used = _fetch_via_sidecar(canonical, sidecar, os.environ.get("LINKFETCH_TOKEN", ""), seconds,
                                                min(ATTEMPT_BYTES, budget.bytes_left))
        budget.bytes_left -= max(used, len(data) if data else 0)  # ir nesėkmės atveju
        return data, reason
    allowed_s = min(LINK_TIMEOUT_S, budget.time_left())
    if allowed_s < 5 or budget.bytes_left <= 0:
        return None, "viršytas laiško nuorodų biudžetas"
    tmp = tempfile.mkdtemp(prefix="uno-link-")
    try:
        os.makedirs(os.path.join(tmp, "dl"), exist_ok=True)
        ctx = multiprocessing.get_context("spawn")
        recv_end, send_end = ctx.Pipe(False)
        proc = ctx.Process(target=_target, args=(canonical, domains, tmp, min(ATTEMPT_BYTES, budget.bytes_left),
                                                 min(CHILD_SOFT_DEADLINE_S, allowed_s - 3), send_end), daemon=True)
        proc.start()
        send_end.close()
        stopped = _wait_child(proc, tmp, allowed_s)
        if stopped:
            _kill_tree(proc, tmp)
            log.info("Nuorodos bandymas nutrauktas (%s) %s", stopped, link_label(canonical))
            return None, "viršytas laukimo laikas" if stopped == "time" else "per didelis atsakymas"
        if not recv_end.poll(1):
            return None, "nuorodos apdoroti nepavyko (procesas baigėsi be rezultato)"
        reason, used = recv_end.recv()
        budget.bytes_left -= int(used)
        out = Path(tmp) / "out.pdf"
        if not out.exists() or out.stat().st_size > MAX_PDF_BYTES:
            return None, reason or "PDF negautas"
        data = out.read_bytes()
        return (data, "") if _is_pdf(data) else (None, "PDF negautas")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
