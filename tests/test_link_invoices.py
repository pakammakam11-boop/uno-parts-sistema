"""Sąskaitos per nuorodą: kanonizavimas, leistini domenai, PDF patikra, maskavimas, watchdog, nepavykusių pastabos."""
import json
import os
import time
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path

import pytest

from app import services
from core import link_invoices, link_safety, mail_import
from tests.test_mail_import import FakeIMAP, _message, imap  # noqa: F401  (imap fixture)

TOKEN = "AbCdEfGh1234567890XyZ"
GOOD = f"https://mail1.agnum.lt/agnum-saskaitos/public/saskaita/{TOKEN}"
D = ["agnum.lt"]
_REAL_CLIENT = __import__("httpx").Client


def _link_mail(*urls: str, attach_pdf: bool = False, subject: str = "Sąskaita Nr. 5") -> bytes:
    msg = EmailMessage()
    msg["From"] = "Dvi kryptys <info@dvikryptys.lt>"
    msg["Subject"] = subject
    msg["Date"] = "Wed, 04 Nov 2026 10:00:00 +0200"
    msg.set_content("Nuoroda: " + " ".join(urls))
    msg.add_alternative("".join(f'<a href="{u.replace("&", "&amp;")}">sąskaita</a>' for u in urls), subtype="html")
    if attach_pdf:
        msg.add_attachment(b"%PDF-att", maintype="application", subtype="pdf", filename="a.pdf")
    return msg.as_bytes()


@pytest.fixture
def fetcher(monkeypatch):
    calls = []

    def fake(url, domains, budget=None):
        calls.append(url)
        return b"%PDF-1.4 link", ""

    monkeypatch.setattr(link_invoices, "fetch_link_pdf", fake)
    monkeypatch.delenv("UNO_INVOICE_LINK_DOMAINS", raising=False)
    real_env = mail_import._env
    monkeypatch.setattr(mail_import, "_env", lambda n: "" if n == "UNO_INVOICE_LINK_DOMAINS" else real_env(n))
    return calls


def R(content, headers=None, status=200):
    """Srautinis atsakymas be išankstinio nuskaitymo/dekodavimo (kaip tikras tinklas)."""
    import httpx

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if isinstance(content, (bytes, bytearray)):
                for i in range(0, len(content), 4096):
                    yield bytes(content[i:i + 4096])
            else:
                yield from content

    return httpx.Response(status, headers=headers or {}, stream=Stream())


def _patch_httpx(monkeypatch, handler):
    import httpx
    real_client = _REAL_CLIENT
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "proxy"}))


# ---- S1: kanonizavimas ----

def test_allowlist_rules():
    assert link_invoices.is_allowed("https://agnum.lt/x", D)
    assert link_invoices.is_allowed("https://mail1.agnum.lt/x", D)
    assert link_invoices.is_allowed("https://AGNUM.LT/x", D)
    assert not link_invoices.is_allowed("http://mail1.agnum.lt/x", D)
    assert not link_invoices.is_allowed("https://evil.com/?u=agnum.lt", D)
    assert link_invoices.parse_domains(None) == ["agnum.lt"]
    assert link_invoices.parse_domains(" A.lt, .b.lt ,") == ["a.lt", "b.lt"]


@pytest.mark.parametrize("url", [
    "https://evilagnum.lt/x",
    "https://agnum.lt.evil.com/x",
    "https://example.com\\@agnum.lt/invoice",
    "https://example.com\\.agnum.lt/x",
    "https://user@agnum.lt/x",
    "https://agnum.lt@evil.com/x",
    "https://agnum.lt:8443/x",
    "https://agnum.lt:/x",
    "https://agnum.lt./x",
    "https://xn--agnum-9ya.lt/x",
    "https://xn--.agnum.lt/x",
    "https://xn--a.agnum.lt​/x",
    "https://agnum.lt/x y",
    "https://agnum.lt/x\r\nHost: evil.com",
    "https://agnum.lt/\x00",
    "https://127.0.0.1/x", "https://2130706433/x", "https://0x7f.1/x", "https://[::1]/x",
    "https://[::ffff:127.0.0.1]/x", "ftp://agnum.lt/x", "//agnum.lt/x", "https:///agnum.lt/x", "", None,
])
def test_canonicalize_rejects_tricks(url):
    assert link_safety.canonicalize(url, D) is None


def test_canonicalize_normalizes_and_is_idempotent():
    out = link_safety.canonicalize("https://MAIL1.Agnum.lt:443/a/b?x=1#frag", D)
    assert out == "https://mail1.agnum.lt/a/b?x=1"
    assert link_safety.canonicalize(out, D) == out
    assert link_safety.canonicalize("https://agnum.lt", D) == "https://agnum.lt/"


def test_ip_literal_never_passes_even_if_listed():
    assert link_safety.canonicalize("https://10.0.0.1/x", ["10.0.0.1"]) is None


# ---- S7: žurnalas, pastabos, failų vardai ----

def test_label_hides_tokens():
    label = link_safety.link_label(GOOD + "?k=secretsecretsecretsecret")
    assert TOKEN not in label and "secret" not in label and "/" not in label
    assert label.startswith("mail1.agnum.lt#") and len(label) == len("mail1.agnum.lt#") + 10


def test_extract_links_html_and_plain():
    links = link_invoices.extract_links(message_from_bytes(_link_mail(GOOD, "https://x.lt/a?b=1&c=2")))
    assert links == [GOOD, "https://x.lt/a?b=1&c=2"]


def test_link_mail_saved_as_pdf_with_metadata(imap, tmp_path, fetcher):  # noqa: F811
    imap.messages = {b"9": _link_mail(GOOD)}
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert len(got) == 1 and got[0]["source"] == "link"
    assert got[0]["file"] == "9_L0.pdf"  # tik uid + indeksas
    assert (tmp_path / got[0]["file"]).read_bytes().startswith(b"%PDF-")
    assert got[0]["sender"].endswith("<info@dvikryptys.lt>") and got[0]["date"] == "2026-11-04"
    assert not (tmp_path / mail_import.LINK_FAILURES_FILE).exists()


def test_only_allowlisted_https_followed_max_three(imap, tmp_path, fetcher):  # noqa: F811
    urls = ["http://mail1.agnum.lt/plain", "https://evil.com/x", GOOD + "1", GOOD + "2", GOOD + "3", GOOD + "4"]
    imap.messages = {b"1": _link_mail(*urls)}
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert fetcher == [GOOD + "1", GOOD + "2", GOOD + "3"] and len(got) == 3
    assert [g["file"] for g in got] == ["1_L0.pdf", "1_L1.pdf", "1_L2.pdf"]


def test_canonical_url_is_what_gets_fetched(imap, tmp_path, fetcher):  # noqa: F811
    imap.messages = {b"1": _link_mail("https://MAIL1.agnum.lt:443/p/x#frag")}
    mail_import.fetch_invoice_pdfs(tmp_path)
    assert fetcher == ["https://mail1.agnum.lt/p/x"]


def test_mail_with_pdf_attachment_ignores_links(imap, tmp_path, fetcher):  # noqa: F811
    imap.messages = {b"1": _link_mail(GOOD, attach_pdf=True)}
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert fetcher == [] and len(got) == 1 and "source" not in got[0]


def test_no_domains_configured_follows_nothing(imap, tmp_path, fetcher, monkeypatch):  # noqa: F811
    real_env = mail_import._env
    monkeypatch.setattr(mail_import, "_env", lambda n: "-" if n == "UNO_INVOICE_LINK_DOMAINS" else real_env(n))
    imap.messages = {b"1": _link_mail(GOOD)}
    assert mail_import.fetch_invoice_pdfs(tmp_path) == [] and fetcher == []


def test_failure_recorded_without_token_or_subject(imap, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(link_invoices, "fetch_link_pdf", lambda url, domains, budget=None: (None, "PDF negautas"))
    imap.messages = {b"1": _link_mail(GOOD, subject=f"Sąskaita Nr. 5 {TOKEN}")}
    assert mail_import.fetch_invoice_pdfs(tmp_path) == []
    raw = (tmp_path / mail_import.LINK_FAILURES_FILE).read_text(encoding="utf-8")
    assert TOKEN not in raw
    assert "Sąskaita" not in raw and "subject" not in raw  # tema į pastabas nepatenka
    assert json.loads(raw) == [{"sender_domain": "dvikryptys.lt", "uid": "1", "host": "mail1.agnum.lt",
                                "reason": "PDF negautas"}]


def test_failure_becomes_warning_finding(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "ROOT", tmp_path)
    monkeypatch.setattr(services, "OUT", tmp_path / "out")
    monkeypatch.setenv("UNO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("UNO_MAIL_AI", "0")
    folder = services.mail_folder()
    folder.mkdir(parents=True)
    (folder.parent / (folder.name + mail_import.LINK_FAILURES_FILE)).write_text(json.dumps(
        [{"sender_domain": "dvikryptys.lt", "uid": "7", "host": "mail1.agnum.lt", "reason": "PDF negautas"}]), encoding="utf-8")
    result = services.build_mail_result(0)
    hits = [f for f in result["_findings"] if f.title == "Sąskaitos nuorodos parsisiųsti nepavyko"]
    assert len(hits) == 1 and hits[0].severity == "warning" and "dvikryptys.lt" in hits[0].detail
    assert "7" in hits[0].detail and "mail1.agnum.lt" in hits[0].detail and "tema" not in hits[0].detail


# ---- S8: charset ir laiško lygio klaidos ----

def test_unknown_charset_decodes_as_utf8():
    raw = b"Content-Type: text/plain; charset=x-nope\r\nContent-Transfer-Encoding: 8bit\r\n\r\n" + GOOD.encode()
    assert link_invoices.extract_links(message_from_bytes(raw)) == [GOOD]


def test_per_mail_error_becomes_warning_and_import_continues(imap, tmp_path, fetcher, monkeypatch):  # noqa: F811
    imap.messages = {b"1": _link_mail(GOOD + "a", subject="blogas"), b"2": _link_mail(GOOD + "b")}
    real = link_invoices.extract_links

    def boom_first(msg):
        if str(msg.get("Subject")) == "blogas":
            raise RuntimeError("sugedo")
        return real(msg)

    monkeypatch.setattr(link_invoices, "extract_links", boom_first)
    got = mail_import.fetch_invoice_pdfs(tmp_path)
    assert len(got) == 1 and got[0]["file"] == "2_L0.pdf"
    failures = json.loads((tmp_path / mail_import.LINK_FAILURES_FILE).read_text(encoding="utf-8"))
    assert "RuntimeError" in failures[0]["reason"]


# ---- S3: dydis ir PDF antraštė ----

def test_http_pdf_accepted_in_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(link_invoices, "_fetch_http", lambda *a: b"%PDF-1 ok")
    monkeypatch.setattr(link_invoices, "_fetch_browser", lambda *a: (None, "x"))
    data, reason, _ = link_invoices._attempt(GOOD, D, str(tmp_path), 1000, 20)
    assert data == b"%PDF-1 ok" and reason == ""


def test_non_pdf_and_oversize_rejected():
    assert not link_invoices._is_pdf(b"<html>")
    assert not link_invoices._is_pdf(b"%PDF-" + b"0" * link_invoices.MAX_PDF_BYTES)
    assert link_invoices._is_pdf(b"%PDF-1")


def test_stream_stops_reading_over_limit(monkeypatch):
    import httpx
    monkeypatch.setattr(link_invoices, "MAX_PDF_BYTES", 1000)
    sent = []

    def body():
        yield b"%PDF-1"
        for _ in range(500):
            sent.append(1)
            yield b"0" * 70000

    _patch_httpx(monkeypatch, lambda r: R(body()))
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None
    assert len(sent) < 5  # skaitymas nutrauktas iškart po ribos, ne po visko


def test_stream_rejects_non_pdf_early(monkeypatch):
    import httpx
    _patch_httpx(monkeypatch, lambda r: R(b"<html>" + b"x" * 500))
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None


def test_stream_accepts_pdf(monkeypatch):
    import httpx
    _patch_httpx(monkeypatch, lambda r: R(b"%PDF-1.7 hello"))
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) == b"%PDF-1.7 hello"


# ---- S9: suspausto turinio riba ----

def _gz(data: bytes, wbits: int = 31) -> bytes:
    import zlib
    c = zlib.compressobj(9, zlib.DEFLATED, wbits)
    return c.compress(data) + c.flush()


def test_gzip_pdf_decoded_with_cap(monkeypatch):
    import httpx
    body = b"%PDF-1.7 " + b"A" * 5000
    _patch_httpx(monkeypatch, lambda r: R(_gz(body), headers={"content-encoding": "gzip"}))
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) == body


def test_deflate_zlib_and_raw_both_accepted(monkeypatch):
    import httpx
    body = b"%PDF-1.7 " + b"B" * 2000
    for wbits in (15, -15):
        _patch_httpx(monkeypatch, lambda r, w=wbits: R(
            _gz(body, w), headers={"content-encoding": "deflate"}))
        assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) == body


def test_gzip_bomb_aborted_without_big_allocation(monkeypatch):
    import httpx
    import tracemalloc
    monkeypatch.setattr(link_invoices, "MAX_PDF_BYTES", 100_000)
    bomb = _gz(b"%PDF-" + b"\0" * 50_000_000)  # ~50 KB žalių, 50 MB išskleista
    _patch_httpx(monkeypatch, lambda r: R(bomb, headers={"content-encoding": "gzip"}))
    tracemalloc.start()
    try:
        assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 5_000_000  # niekada nesukaupta ~50 MB


@pytest.mark.parametrize("encoding", ["gzip, gzip", "br", "gzip, deflate", "compress", "zstd"])
def test_unsupported_or_stacked_encodings_rejected(monkeypatch, encoding):
    import httpx
    seen = []

    def handler(r):
        seen.append(1)
        return R(_gz(b"%PDF-1 x"), headers={"content-encoding": encoding})

    _patch_httpx(monkeypatch, handler)
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None


def test_corrupt_gzip_rejected(monkeypatch):
    import httpx
    _patch_httpx(monkeypatch, lambda r: R(b"nope" * 20, headers={"content-encoding": "gzip"}))
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None


# ---- S10: naršyklė negali atsisiųsti ar perskaityti didelio turinio ----

def test_browser_context_has_downloads_disabled_and_no_save_as():
    import inspect
    src = inspect.getsource(link_invoices._fetch_browser)
    assert "accept_downloads=False" in src
    assert "save_as" not in inspect.getsource(link_invoices) and ".body()" not in inspect.getsource(link_invoices)


def test_blob_hook_uses_saved_native_getter_and_caps_store():
    js = link_invoices.BLOB_HOOK
    assert "getOwnPropertyDescriptor(Blob.prototype, 'size')" in js and "sizeGet.call(b)" in js
    assert "store.length > 20" in js  # tik paskutiniai 20
    assert js.index("size > limit") < js.index("bufFn.call(b)")  # dydis prieš pilną skaitymą
    assert "%PDF-" in js


def test_read_blob_validates_pdf():
    import base64

    class Page:
        def __init__(self, value):
            self.value = value

        def evaluate(self, js, limit):
            assert limit == link_invoices.MAX_PDF_BYTES
            return self.value

    assert link_invoices._read_blob(Page(base64.b64encode(b"%PDF-1 ok").decode())) == b"%PDF-1 ok"
    assert link_invoices._read_blob(Page(base64.b64encode(b"<html>").decode())) is None
    assert link_invoices._read_blob(Page(None)) is None
    assert link_invoices._read_blob(Page("A" * (link_invoices.MAX_PDF_BYTES * 2))) is None  # per ilgas atsakymas


def test_click_collects_candidate_url_and_refetches_with_all_cookies(monkeypatch):
    calls = []

    class Btn:
        first = type("F", (), {"click": lambda self, timeout=0: None})()
        count = lambda self: 1  # noqa: E731

    class Page:
        def get_by_role(self, *a, **k):
            return Btn()

        def wait_for_timeout(self, ms):
            pass

    class Ctx:
        pages = []

        def cookies(self, url=None):
            return [{"name": "sid", "value": "abc", "domain": "mail1.agnum.lt", "path": "/"}]

    def fake_fetch(url, domains, deadline, proxy_url, cookies=None):
        calls.append((url, {c["name"]: c["value"] for c in cookies}, proxy_url))
        return b"%PDF-1 via reader"

    monkeypatch.setattr(link_invoices, "_fetch_http", fake_fetch)
    data = link_invoices._click_and_collect(Page(), Ctx(), [GOOD], D, time.monotonic() + 10, "http://127.0.0.1:1")
    assert data == b"%PDF-1 via reader" and calls == [(GOOD, {"sid": "abc"}, "http://127.0.0.1:1")]


# ---- S15/S20: sausainiai griežtai pagal host/path, parenkami kiekvienam hop ----

COOKIES = [{"name": "sid", "value": "secret1", "domain": "mail1.agnum.lt", "path": "/agnum"},   # host-only
           {"name": "wide", "value": "w", "domain": ".agnum.lt", "path": "/"}]


def test_cookies_for_strict_host_only_and_domain_rules():
    f = link_invoices._cookies_for
    assert f(COOKIES, "https://mail1.agnum.lt/agnum/p") == "sid=secret1; wide=w"
    assert f(COOKIES, "https://other.agnum.lt/agnum/x") == "wide=w"                 # kitas hostas
    assert f(COOKIES, "https://child.mail1.agnum.lt/agnum/x") == "wide=w"           # S20: host-only ne subdomenui
    assert f(COOKIES, "https://agnum.lt/") == "wide=w"                              # domeno sausainis ir pačiam domenui
    assert f(COOKIES, "https://mail1.agnum.lt/kita") == "wide=w"                    # kelias
    assert f(COOKIES, "https://mail1.agnum.lt/agnumx/p") == "wide=w"                # kelio riba
    assert f(COOKIES, "http://mail1.agnum.lt/agnum/p") == ""                        # ne https
    assert f(COOKIES, "https://evilagnum.lt/") == ""                                # ne subdomenas
    assert f(None, "https://mail1.agnum.lt/") == ""


def test_cookie_not_sent_to_child_or_sibling_host_on_redirect(monkeypatch):
    import httpx
    seen = []
    hops = {"mail1.agnum.lt": "https://child.mail1.agnum.lt/agnum/x", "child.mail1.agnum.lt": "https://other.agnum.lt/agnum/y"}

    def handler(request):
        seen.append((request.url.host, request.headers.get("cookie", "")))
        if request.url.host in hops:
            return httpx.Response(302, headers={"location": hops[request.url.host]})
        return R(b"%PDF-1 ok")

    _patch_httpx(monkeypatch, handler)
    out = link_invoices._fetch_http("https://mail1.agnum.lt/agnum/p", D, time.monotonic() + 30, None, COOKIES)
    assert out == b"%PDF-1 ok"
    assert seen[0] == ("mail1.agnum.lt", "sid=secret1; wide=w")
    assert seen[1] == ("child.mail1.agnum.lt", "wide=w")
    assert seen[2] == ("other.agnum.lt", "wide=w")


# ---- S13/S16: tikra naršyklė (praleidžiama, jei Chromium neįdiegtas) ----

@pytest.fixture(scope="module")
def local_site():
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/popup":
                body = (b"<html><body><script>"
                        b"URL.createObjectURL(new Blob(['%PDF-1.4 from popup'], {type: 'application/pdf'}));"
                        b"</script>popup</body></html>")
            else:
                body = (b"<html><body><button onclick=\"window.open('/popup')\">Parsisi\xc5\xb3sti</button></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def browser_ctx():
    pw_mod = pytest.importorskip("playwright.sync_api")
    pw = pw_mod.sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception:
        pw.stop()
        pytest.skip("Chromium neįdiegtas")
    ctx = browser.new_context(accept_downloads=False)
    ctx.add_init_script(link_invoices.BLOB_HOOK)
    yield ctx
    browser.close()
    pw.stop()


def test_popup_blob_pdf_is_collected(local_site, browser_ctx):
    page = browser_ctx.new_page()
    page.goto(local_site + "/")
    data = link_invoices._click_and_collect(page, browser_ctx, [], D, time.monotonic() + 15, "http://127.0.0.1:1")
    assert data == b"%PDF-1.4 from popup"


def test_spoofed_blob_size_cannot_bypass_limit(local_site, browser_ctx):
    page = browser_ctx.new_page()
    page.goto(local_site + "/nera")
    page.evaluate("""() => {
        Object.defineProperty(Blob.prototype, 'size', {get() { return 5; }});
        URL.createObjectURL(new Blob(['%PDF-' + 'a'.repeat(4000)], {type: 'application/pdf'}));
    }""")
    assert page.evaluate(link_invoices.BLOB_READ, 1024) is None  # tikras dydis > riba
    assert page.evaluate(link_invoices.BLOB_READ, 8192) is not None


def test_blob_store_keeps_only_last_20(local_site, browser_ctx):
    page = browser_ctx.new_page()
    page.goto(local_site + "/nera")
    page.evaluate("""() => { URL.createObjectURL(new Blob(['%PDF-first'])); 
        for (let i = 0; i < 25; i++) URL.createObjectURL(new Blob(['not a pdf ' + i])); }""")
    assert page.evaluate(link_invoices.BLOB_READ, 1024) is None  # pirmas PDF išstumtas iš 20 vietų


# ---- sidecar kliento pusė ----

def _patch_sidecar(monkeypatch, handler):
    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL_CLIENT(
        transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "proxy"}))


def test_sidecar_used_when_env_set_sends_token_and_remaining_budget(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    monkeypatch.setenv("LINKFETCH_TOKEN", "tok123456789012345")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return R(b"%PDF-1 sidecar", headers={"content-type": "application/pdf", "x-bytes-used": "5000"})

    _patch_sidecar(monkeypatch, handler)
    monkeypatch.setattr(link_invoices.multiprocessing, "get_context", lambda *a: pytest.fail("vaiko procesas"))
    budget = link_invoices.LinkBudget(deadline=time.monotonic() + 40, bytes_left=7_000_000)
    assert link_invoices.fetch_link_pdf(GOOD, D, budget) == (b"%PDF-1 sidecar", "")
    assert seen["auth"] == "Bearer tok123456789012345" and seen["url"] == "http://linkfetch:8099/fetch"
    assert seen["body"]["url"] == GOOD
    assert 38 <= seen["body"]["max_seconds"] <= 40          # S19: likęs laikas
    assert seen["body"]["max_bytes"] == 7_000_000           # S19: likę baitai (mažiau nei bandymo riba)
    assert budget.bytes_left == 7_000_000 - 5000            # sunaudoti baitai pagal sidecar


def test_failure_charges_bytes_used_from_error_json(monkeypatch):
    import httpx
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    _patch_sidecar(monkeypatch, lambda r: R(json.dumps({"error": "PDF negautas", "bytes_used": 12_345_678}).encode(), status=422))
    budget = link_invoices.LinkBudget()
    assert link_invoices.fetch_link_pdf(GOOD, D, budget) == (None, "PDF negautas")
    assert budget.bytes_left == link_invoices.MAIL_BUDGET_BYTES - 12_345_678


def test_three_failed_links_cannot_exceed_mail_budget(monkeypatch):
    import httpx
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    asked = []

    def handler(request):
        body = json.loads(request.content)
        asked.append(body["max_bytes"])
        return R(json.dumps({"error": "x", "bytes_used": body["max_bytes"]}).encode(), status=422)  # kiekvienas sunaudoja viską

    _patch_sidecar(monkeypatch, handler)
    budget = link_invoices.LinkBudget(bytes_left=70 * 1024 * 1024)
    for _ in range(3):
        link_invoices.fetch_link_pdf(GOOD, D, budget)
    assert asked == [40 * 1024 * 1024, 30 * 1024 * 1024]  # trečiam bandymui biudžeto nebelieka, užklausos nėra
    assert sum(asked) <= 70 * 1024 * 1024  # niekada daugiau nei laiško biudžetas
    assert budget.bytes_left <= 0


def test_sidecar_error_reason_non_pdf_and_oversize(monkeypatch):
    import httpx
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    _patch_sidecar(monkeypatch, lambda r: R(json.dumps({"error": "PDF negautas"}).encode(), status=422))
    assert link_invoices.fetch_link_pdf(GOOD, D) == (None, "PDF negautas")
    _patch_sidecar(monkeypatch, lambda r: R(json.dumps({"error": "paslauga užimta"}).encode(), status=503))
    assert link_invoices.fetch_link_pdf(GOOD, D) == (None, "paslauga užimta")
    _patch_sidecar(monkeypatch, lambda r: R(b"<html>", headers={"content-type": "application/pdf"}))
    assert link_invoices.fetch_link_pdf(GOOD, D)[0] is None
    monkeypatch.setattr(link_invoices, "MAX_PDF_BYTES", 100)
    _patch_sidecar(monkeypatch, lambda r: R(b"%PDF-" + b"0" * 5000, headers={"content-type": "application/pdf"}))
    assert link_invoices.fetch_link_pdf(GOOD, D)[0] is None


def test_sidecar_unreachable_gives_warning_without_url(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://127.0.0.1:1")
    data, reason = link_invoices.fetch_link_pdf(GOOD, D)
    assert data is None and "nepasiekiama" in reason and TOKEN not in reason


def test_sidecar_rejects_bad_url_before_network(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    monkeypatch.setattr(link_invoices, "_fetch_via_sidecar", lambda *a, **k: pytest.fail("tinklas"))
    assert link_invoices.fetch_link_pdf("https://evil.com/x", D) == (None, "nuoroda neleistina")


# ---- S17: klaidų kūnas skaitomas riboto dydžio srautu ----

def test_error_body_read_is_bounded_raw_and_decoded(monkeypatch):
    import httpx
    consumed = []

    def big_error():
        for _ in range(2000):
            consumed.append(1)
            yield b"x" * 1024  # 2 MB nesuspausto "klaidos" kūno

    def handler(request):
        return R(big_error(), status=500)

    _patch_sidecar(monkeypatch, handler)
    data, reason, used = link_invoices._fetch_via_sidecar(GOOD, "http://linkfetch:8099", "t")
    assert data is None and reason == "nuorodų paslauga atsakė 500"
    assert len(consumed) < 20  # perskaityta kelios kilobaitai, ne visi 2 MB


def test_error_body_gzip_bomb_aborted(monkeypatch):
    monkeypatch.setattr(link_invoices, "ERROR_BODY_LIMIT", 4096)
    bomb = _gz(b"A" * 50_000_000)
    _patch_sidecar(monkeypatch, lambda r: R(bomb, headers={"content-encoding": "gzip"}, status=500))
    import tracemalloc
    tracemalloc.start()
    try:
        data, reason, used = link_invoices._fetch_via_sidecar(GOOD, "http://linkfetch:8099", "t")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert data is None and "500" in reason and peak < 2_000_000


# ---- S18: absoliutus terminas (lėtas lašinimas) ----

@pytest.fixture
def drip_server():
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(self.server.mode_status)
            self.send_header("Content-Type", self.server.mode_ctype)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                self.wfile.write(b"%PDF-1" if self.server.mode_status == 200 else b"{")
                for _ in range(600):
                    time.sleep(0.1)
                    self.wfile.write(b"0")
                    self.wfile.flush()
            except OSError:
                pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    httpd.mode_status, httpd.mode_ctype = 200, "application/pdf"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


@pytest.mark.parametrize("status,ctype", [(200, "application/pdf"), (500, "application/json")])
def test_slow_drip_sidecar_aborted_by_absolute_deadline(drip_server, monkeypatch, status, ctype):
    drip_server.mode_status, drip_server.mode_ctype = status, ctype
    monkeypatch.setattr(link_invoices, "SIDECAR_TIMEOUT_S", 2)
    started = time.monotonic()
    data, reason, used = link_invoices._fetch_via_sidecar(
        GOOD, f"http://127.0.0.1:{drip_server.server_address[1]}", "t", seconds=1, max_bytes=1000)
    elapsed = time.monotonic() - started
    assert data is None
    assert elapsed < 4.5  # 1 s + 8 s riba apkirpta SIDECAR_TIMEOUT_S=2; lašinimas būtų tęsęsis 60 s


def test_tmp_dir_over_cap_kills_child(monkeypatch):
    monkeypatch.setattr(link_invoices, "MAX_TMP_BYTES", 1000)
    monkeypatch.setattr(link_invoices, "LINK_TIMEOUT_S", 20)
    started = time.monotonic()
    data, reason = link_invoices.fetch_link_pdf(GOOD, D, _target=fill_disk_target)
    assert data is None and reason == "per didelis atsakymas" and time.monotonic() - started < 15


def fill_disk_target(url, domains, tmp, budget_bytes, soft_s, conn):  # vykdoma spawn vaiko procese
    import time as _t
    from pathlib import Path as _P
    (_P(tmp) / "big.bin").write_bytes(b"x" * 50_000)
    _t.sleep(300)


# ---- S1/S2: peradresavimai ----

def test_http_redirect_to_foreign_domain_not_followed(monkeypatch):
    import httpx
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.com/x"})

    _patch_httpx(monkeypatch, handler)
    assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None and len(seen) == 1


def test_redirect_to_http_or_userinfo_not_followed(monkeypatch):
    import httpx
    for target in ("http://mail1.agnum.lt/plain", "https://mail1.agnum.lt\\@evil.com/", "https://u@mail1.agnum.lt/"):
        seen = []

        def handler(request, target=target):
            seen.append(1)
            return httpx.Response(302, headers={"location": target})

        _patch_httpx(monkeypatch, handler)
        assert link_invoices._fetch_http(GOOD, D, time.monotonic() + 30) is None and len(seen) == 1


# ---- biudžetas ir atmesti URL ----

def test_rejected_url_never_spawns_process(monkeypatch):
    called = []
    monkeypatch.setattr(link_invoices.multiprocessing, "get_context", lambda *a: called.append(1))
    assert link_invoices.fetch_link_pdf("https://evil.com/x", D) == (None, "nuoroda neleistina")
    assert not called


def test_budget_exhausted_returns_warning():
    budget = link_invoices.LinkBudget(bytes_left=0)
    assert link_invoices.fetch_link_pdf(GOOD, D, budget) == (None, "viršytas laiško nuorodų biudžetas")


# ---- S4: watchdog su tikru procesu ----

def hang_target(url, domains, tmp, budget_bytes, soft_s, conn):  # vykdoma spawn vaiko procese
    import subprocess
    import sys
    import time as _t
    from pathlib import Path as _P
    from core import link_invoices as li
    li._start_pid_recorder(tmp, 0.2)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    (_P(tmp) / "pid").write_text(str(child.pid))
    _t.sleep(300)


def detached_target(url, domains, tmp, budget_bytes, soft_s, conn):  # Linux: kaip Playwright Chromium (setsid)
    import subprocess
    import sys
    import time as _t
    from pathlib import Path as _P
    from core import link_invoices as li
    li._start_pid_recorder(tmp, 0.2)
    # dvigubas fork: tarpinis procesas išeina, anūkas lieka našlaitis kitoje sesijoje
    code = ("import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'],start_new_session=True);"
            "open(sys.argv[1],'w').write(str(p.pid))")
    subprocess.run([sys.executable, "-c", code, str(_P(tmp) / "pid")])
    _t.sleep(300)


def ok_target(url, domains, tmp, budget_bytes, soft_s, conn):  # vykdoma spawn vaiko procese
    from pathlib import Path as _P
    (_P(tmp) / "out.pdf").write_bytes(b"%PDF-1 from child")
    conn.send(("", 123))
    conn.close()


def _pid_alive(pid: int) -> bool:
    import subprocess
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_child_process_result_roundtrip():
    budget = link_invoices.LinkBudget()
    assert link_invoices.fetch_link_pdf(GOOD, D, budget, _target=ok_target) == (b"%PDF-1 from child", "")
    assert budget.bytes_left == link_invoices.MAIL_BUDGET_BYTES - 123


def test_watchdog_kills_process_tree_and_cleans_temp(monkeypatch):
    import tempfile
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(**kw):
        created.append(real_mkdtemp(**kw))
        return created[-1]

    monkeypatch.setattr(link_invoices.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(link_invoices, "LINK_TIMEOUT_S", 8)
    pid_file_seen = {}
    real_rmtree = link_invoices.shutil.rmtree

    def spy_rmtree(path, **kw):
        pidf = Path(path) / "pid"
        if pidf.exists():
            pid_file_seen["pid"] = int(pidf.read_text())
        real_rmtree(path, **kw)

    monkeypatch.setattr(link_invoices.shutil, "rmtree", spy_rmtree)
    started = time.monotonic()
    data, reason = link_invoices.fetch_link_pdf(GOOD, D, _target=hang_target)
    elapsed = time.monotonic() - started
    assert data is None and "laukimo" in reason
    assert elapsed < 8 + 10
    assert created and not os.path.exists(created[0])
    assert "pid" in pid_file_seen
    time.sleep(1)
    assert not _pid_alive(pid_file_seen["pid"])  # anūkas (naršyklės pakaitalas) nužudytas


@pytest.mark.skipif(os.name == "nt", reason="setsid tik Linux")
def test_watchdog_kills_detached_orphan_in_other_session(monkeypatch):
    monkeypatch.setattr(link_invoices, "LINK_TIMEOUT_S", 8)
    seen = {}
    real_rmtree = link_invoices.shutil.rmtree

    def spy(path, **kw):
        pidf = Path(path) / "pid"
        if pidf.exists():
            seen["pid"] = int(pidf.read_text())
        real_rmtree(path, **kw)

    monkeypatch.setattr(link_invoices.shutil, "rmtree", spy)
    data, reason = link_invoices.fetch_link_pdf(GOOD, D, _target=detached_target)
    assert data is None and "laukimo" in reason and "pid" in seen
    time.sleep(1)
    assert not _pid_alive(seen["pid"])


@pytest.mark.live
def test_live_agnum_link():
    """Reikia UNO_LIVE_AGNUM_URL (tikra nuoroda) ir įdiegto Playwright Chromium."""
    url = os.environ.get("UNO_LIVE_AGNUM_URL")
    if not url:
        pytest.skip("nenustatyta UNO_LIVE_AGNUM_URL")
    data, reason = link_invoices.fetch_link_pdf(url, D)
    assert data and data[:5] == b"%PDF-", reason


# ---- Ratas 5: S19 nutrūkęs atsakymas, S20/S23 Set-Cookie saugykla, S21 lėtos antraštės, S22 atmintis ----

def _broken_pdf_stream():
    import httpx

    def gen():
        yield b"%PDF-1 start"
        raise httpx.ReadError("nutrūko")
    return gen()


def test_broken_stream_keeps_known_bytes_used(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    _patch_sidecar(monkeypatch, lambda r: R(_broken_pdf_stream(), headers={"content-type": "application/pdf",
                                                                           "x-bytes-used": "1234"}))
    budget = link_invoices.LinkBudget(bytes_left=70 * 1024 * 1024)
    data, reason = link_invoices.fetch_link_pdf(GOOD, D, budget)
    assert data is None and "nepasiekiama" in reason
    assert budget.bytes_left == 70 * 1024 * 1024 - 1234


def test_broken_stream_without_known_usage_charges_reserved_budget(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    asked = []

    def handler(request):
        asked.append(json.loads(request.content)["max_bytes"])
        return R(_broken_pdf_stream(), headers={"content-type": "application/pdf"})  # be X-Bytes-Used

    _patch_sidecar(monkeypatch, handler)
    budget = link_invoices.LinkBudget(bytes_left=70 * 1024 * 1024)
    for _ in range(3):
        link_invoices.fetch_link_pdf(GOOD, D, budget)
    assert asked == [40 * 1024 * 1024, 30 * 1024 * 1024]  # trečiam biudžeto nebelieka
    assert budget.bytes_left <= 0


def test_missing_bytes_used_in_error_json_charges_reserved(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://linkfetch:8099")
    _patch_sidecar(monkeypatch, lambda r: R(b'{"error": "x"}', status=422))
    budget = link_invoices.LinkBudget(bytes_left=70 * 1024 * 1024)
    link_invoices.fetch_link_pdf(GOOD, D, budget)
    assert budget.bytes_left == 30 * 1024 * 1024


def test_connect_failure_charges_nothing(monkeypatch):
    monkeypatch.setenv("LINKFETCH_URL", "http://127.0.0.1:1")
    budget = link_invoices.LinkBudget()
    link_invoices.fetch_link_pdf(GOOD, D, budget)
    assert budget.bytes_left == link_invoices.MAIL_BUDGET_BYTES


def _mock_hops(monkeypatch, plan):
    """plan: {(host, path): (status, set_cookie_list, location)}; grąžina matytų užklausų sąrašą (host, path, cookie)."""
    import httpx
    seen = []

    def handler(request):
        host = request.url.host
        seen.append((host, request.url.path, request.headers.get("cookie", "")))
        status, set_cookies, location = plan[(host, request.url.path)]
        headers = [("set-cookie", c) for c in set_cookies]
        if location:
            headers.append(("location", location))
        if status == 200:
            return R(b"%PDF-1 ok", headers=headers)
        return httpx.Response(status, headers=headers)

    _patch_httpx(monkeypatch, handler)
    return seen


def test_host_only_set_cookie_from_redirect_not_sent_to_child_host(monkeypatch):
    seen = _mock_hops(monkeypatch, {
        ("mail1.agnum.lt", "/a"): (302, ["sid=one; Path=/"], "https://child.mail1.agnum.lt/b"),
        ("child.mail1.agnum.lt", "/b"): (302, [], "https://mail1.agnum.lt/c"),
        ("mail1.agnum.lt", "/c"): (200, [], None)})
    assert link_invoices._fetch_http("https://mail1.agnum.lt/a", D, time.monotonic() + 30) == b"%PDF-1 ok"
    assert seen[1] == ("child.mail1.agnum.lt", "/b", "")          # host-only: ne subdomenui (httpx jar išjungtas)
    assert seen[2] == ("mail1.agnum.lt", "/c", "sid=one")         # tam pačiam hostui siunčiamas


def test_set_cookie_domain_attribute_reaches_subdomain_and_update_is_used(monkeypatch):
    seen = _mock_hops(monkeypatch, {
        ("mail1.agnum.lt", "/a"): (302, ["sid=old; Domain=agnum.lt; Path=/"], "https://mail1.agnum.lt/b"),
        ("mail1.agnum.lt", "/b"): (302, ["sid=new; Domain=agnum.lt; Path=/"], "https://child.mail1.agnum.lt/c"),
        ("child.mail1.agnum.lt", "/c"): (200, [], None)})
    link_invoices._fetch_http("https://mail1.agnum.lt/a", D, time.monotonic() + 30,
                              cookies=[{"name": "sid", "value": "seed", "domain": ".agnum.lt", "path": "/"}])
    assert seen[0][2] == "sid=seed"
    assert seen[1][2] == "sid=old"       # atnaujinta iš Set-Cookie
    assert seen[2][2] == "sid=new"       # S23: kitas hop naudoja naujausią reikšmę, ne pradinę


def test_set_cookie_foreign_domain_rejected_and_max_age_zero_deletes():
    store = link_invoices._CookieStore()
    store.update("https://mail1.agnum.lt/x", ["a=1; Domain=evil.com", "b=2; Path=/", "c=3; Domain=agnum.lt; Path=/"])
    assert store.header("https://mail1.agnum.lt/x") == "b=2; c=3"
    assert store.header("https://evil.com/x") == ""
    store.update("https://mail1.agnum.lt/x", ["b=; Max-Age=0; Path=/"])
    assert store.header("https://mail1.agnum.lt/x") == "c=3"


def test_slow_headers_within_attempt_time_are_accepted():
    """S21: sidecar siunčia antraštes tik baigęs darbą (čia 5,6 s > senas 5 s skaitymo limitas)."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(5.6)
            body = b"%PDF-1 slow but fine"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("X-Bytes-Used", "99")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        data, reason, used = link_invoices._fetch_via_sidecar(
            GOOD, f"http://127.0.0.1:{httpd.server_address[1]}", "t", seconds=20, max_bytes=10_000)
    finally:
        httpd.shutdown()
    assert data == b"%PDF-1 slow but fine" and used == 99


def test_chunked_pdf_memory_stays_near_payload(monkeypatch):
    """S22: 256 KiB PDF mažais fragmentais: vienas bytearray, ne dešimtys tūkstančių objektų + join."""
    import tracemalloc
    payload = b"%PDF-" + b"x" * (256 * 1024 - 5)

    def chunks():
        for i in range(0, len(payload), 4):
            yield payload[i:i + 4]

    _patch_httpx(monkeypatch, lambda r: R(chunks()))
    tracemalloc.start()
    try:
        data = link_invoices._fetch_http(GOOD, D, time.monotonic() + 60)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert data == payload
    assert peak < 1_000_000


# ---- Agnum be naršyklės ----

def test_agnum_token_only_for_agnum_invoice_path():
    tok = "Ab12Cd34Ef56Gh78"
    assert link_invoices._agnum_token(f"https://mail1.agnum.lt/agnum-saskaitos/public/saskaita/{tok}") == tok
    assert link_invoices._agnum_token(f"https://agnum.lt.evil.lt/agnum-saskaitos/public/saskaita/{tok}") is None
    assert link_invoices._agnum_token("https://mail1.agnum.lt/kita/vieta") is None


def test_agnum_api_used_before_browser(monkeypatch):
    monkeypatch.setattr(link_invoices, "fetch_agnum_api", lambda url, domains: b"%PDF-1.4 x")
    monkeypatch.setenv("UNO_LINK_BROWSER", "0")
    data, reason = link_invoices.fetch_link_pdf(
        "https://mail1.agnum.lt/agnum-saskaitos/public/saskaita/Ab12Cd34Ef56Gh78", ["agnum.lt"])
    assert data == b"%PDF-1.4 x" and reason == ""


def test_no_browser_reports_agnum_failure(monkeypatch):
    monkeypatch.setattr(link_invoices, "fetch_agnum_api", lambda url, domains: None)
    monkeypatch.setenv("UNO_LINK_BROWSER", "0")
    data, reason = link_invoices.fetch_link_pdf(
        "https://mail1.agnum.lt/agnum-saskaitos/public/saskaita/Ab12Cd34Ef56Gh78", ["agnum.lt"])
    assert data is None and "Agnum" in reason
