"""linkfetch sidecar serveris: autentifikacija, vienas užklausimas vienu metu, watchdog, klaidos be URL."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from tools import linkfetch_server as srv

TOKEN = "t" * 32
GOOD = "https://mail1.agnum.lt/agnum-saskaitos/public/saskaita/AbCdEfGh1234567890"


@pytest.fixture
def server(monkeypatch):
    httpd = srv.make_server(TOKEN, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(base, body=None, token=TOKEN, path="/fetch", raw=None):
    data = raw if raw is not None else json.dumps(body or {"url": GOOD}).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


def test_refuses_to_start_without_strong_token():
    with pytest.raises(SystemExit):
        srv.make_server("", "127.0.0.1", 0)
    with pytest.raises(SystemExit):
        srv.make_server("short", "127.0.0.1", 0)


def test_healthz_needs_no_token(server):
    with urllib.request.urlopen(server + "/healthz", timeout=5) as resp:
        assert resp.status == 200


def test_wrong_or_missing_token_rejected_before_any_work(server, monkeypatch):
    monkeypatch.setattr(srv, "run_attempt", lambda *a: pytest.fail("neturėjo vykti"))
    assert _post(server, token="wrong")[0] == 401
    assert _post(server, token="")[0] == 401


def test_fetch_returns_pdf(server, monkeypatch):
    monkeypatch.setattr(srv, "run_attempt", lambda url, domains, *a: (b"%PDF-1 hello", "", 777))
    status, ctype, body = _post(server)
    assert status == 200 and ctype == "application/pdf" and body == b"%PDF-1 hello"
    # X-Bytes-Used antraštė tikrinama atskirai


def test_failure_json_without_url(server, monkeypatch):
    monkeypatch.setattr(srv, "run_attempt", lambda url, domains, *a: (None, "PDF negautas", 4242))
    status, ctype, body = _post(server)
    assert status == 422 and json.loads(body) == {"error": "PDF negautas", "bytes_used": 4242} and "AbCdEf" not in body.decode()


def test_exception_becomes_error_without_message(server, monkeypatch):
    def boom(url, domains, *a):
        raise RuntimeError(f"viduje buvo {GOOD}")

    monkeypatch.setattr(srv, "run_attempt", boom)
    status, _, body = _post(server)
    assert status == 422 and "RuntimeError" in body.decode() and "AbCdEf" not in body.decode()


def test_bad_requests(server, monkeypatch):
    monkeypatch.setattr(srv, "run_attempt", lambda *a: pytest.fail("neturėjo vykti"))
    assert _post(server, raw=b"not json")[0] == 400
    assert _post(server, raw=b"{}")[0] == 400
    assert _post(server, raw=b"x" * (srv.MAX_BODY + 1))[0] == 400
    assert _post(server, path="/kitas")[0] == 404


def test_run_attempt_rejects_disallowed_url_without_browser(monkeypatch):
    monkeypatch.setattr(srv.link_invoices, "_attempt", lambda *a: pytest.fail("naršyklė"))
    assert srv.run_attempt("https://evil.com/x", ["agnum.lt"]) == (None, "nuoroda neleistina", 0)


def test_only_one_request_at_a_time(server, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(url, domains, *a):
        started.set()
        release.wait(10)
        return b"%PDF-1 slow", "", 0

    monkeypatch.setattr(srv, "run_attempt", slow)
    first = []
    t = threading.Thread(target=lambda: first.append(_post(server)))
    t.start()
    assert started.wait(5)
    status, _, body = _post(server)
    assert status == 503 and "užimta" in body.decode()
    release.set()
    t.join(10)
    assert first[0][0] == 200


def test_watchdog_fires_when_attempt_exceeds_limit(server, monkeypatch):
    fired = threading.Event()
    monkeypatch.setattr(srv, "WATCHDOG_S", 0.3)
    monkeypatch.setattr(srv, "_watchdog_kill", lambda: fired.set())
    monkeypatch.setattr(srv, "run_attempt", lambda url, domains, *a: (time.sleep(1.0), (None, "x", 0))[1])
    _post(server)
    assert fired.is_set()


def test_watchdog_not_fired_for_fast_attempt(server, monkeypatch):
    fired = threading.Event()
    monkeypatch.setattr(srv, "WATCHDOG_S", 0.5)
    monkeypatch.setattr(srv, "_watchdog_kill", lambda: fired.set())
    monkeypatch.setattr(srv, "run_attempt", lambda url, domains, *a: (b"%PDF-1", "", 0))
    _post(server)
    time.sleep(0.8)
    assert not fired.is_set()


def test_watchdog_exit_code_is_70():
    codes = []
    srv._watchdog_kill(_exit=codes.append)
    assert codes == [70]


def test_bytes_used_header_and_budget_passthrough(server, monkeypatch):
    seen = {}

    def fake(url, domains, max_seconds=None, max_bytes=None):
        seen.update(seconds=max_seconds, bytes=max_bytes)
        return b"%PDF-1 x", "", 321

    monkeypatch.setattr(srv, "run_attempt", fake)
    req = urllib.request.Request(server + "/fetch", method="POST", data=json.dumps(
        {"url": GOOD, "max_seconds": 12, "max_bytes": 3456}).encode(), headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.headers["X-Bytes-Used"] == "321"
    assert seen == {"seconds": 12, "bytes": 3456}


def test_run_attempt_uses_min_of_own_and_given_limits(monkeypatch):
    got = {}

    def fake_attempt(url, domains, tmp, budget, soft):
        got.update(budget=budget, soft=soft)
        return None, "x", 9

    monkeypatch.setattr(srv.link_invoices, "_attempt", fake_attempt)
    own_b, own_s = srv.link_invoices.ATTEMPT_BYTES, srv.link_invoices.CHILD_SOFT_DEADLINE_S
    assert srv.run_attempt(GOOD, ["agnum.lt"], 10, 1000) == (None, "x", 9)
    assert got == {"budget": 1000, "soft": 10}                      # kliento mažesni limitai galioja
    srv.run_attempt(GOOD, ["agnum.lt"], 9999, 10 ** 12)
    assert got == {"budget": own_b, "soft": own_s}                  # didesni nei savi: apkarpomi
    srv.run_attempt(GOOD, ["agnum.lt"], "blogai", -5)
    assert got == {"budget": own_b, "soft": own_s}                  # netinkami: savi limitai
