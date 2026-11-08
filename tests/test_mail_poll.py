"""Pašto procesas (tools.mail_poll) ir app sinchronizacija: IMAP ir AI užmokinti."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, services
from core import mail_import
from tools import mail_poll


@pytest.fixture
def mailenv(monkeypatch, tmp_path):
    # Be Windows registro atsarginio kelio (Pauliaus PC turi tikrus UNO_IMAP_* naudotojo kintamuosius).
    monkeypatch.setattr(mail_import, "_env", lambda name: __import__("os").environ.get(name, "").strip())
    monkeypatch.setenv("UNO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("UNO_MAIL_AI", "0")
    monkeypatch.setenv("UNO_IMAP_USER", "x@example.lt")
    monkeypatch.setenv("UNO_IMAP_PASSWORD", "x")
    return tmp_path


def _fake_fetch(files):
    def fetch(dest, *, days=30, limit=50):
        out = []
        for name, data in files.items():
            (dest / name).write_bytes(data)
            out.append({"file": name})
        return out
    return fetch


def _raise(exc):
    def fetch(dest, *, days=30, limit=50):
        raise exc
    return fetch


def _status(tmp):
    return json.loads((tmp / "mail_status.json").read_text(encoding="utf-8"))


def test_poll_once_success_writes_status(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    code, status = mail_poll.poll_once()
    assert code == 0 and status["ok"] is True and status["pdf_count"] == 0
    saved = _status(mailenv)
    assert saved["ok"] and saved["consecutive_failures"] == 0 and saved["last_ok_at"]


def test_poll_once_reuses_button_functions(mailenv, monkeypatch):
    calls = []
    monkeypatch.setattr(services, "download_mail_pdfs", lambda days: calls.append(days) or [])
    monkeypatch.setattr(services, "build_mail_result", lambda n, days: calls.append(("build", n, days)) or
                        {"_findings": [], "kpis": [{"label": "Nuskaityta sąskaitų", "value": "0"}]})
    mail_poll.poll_once(7)
    assert calls == [7, ("build", 0, 7)]


def test_not_configured_exit_2(mailenv, monkeypatch):
    monkeypatch.delenv("UNO_IMAP_USER")
    monkeypatch.delenv("UNO_IMAP_PASSWORD")
    code, status = mail_poll.poll_once()
    assert code == mail_poll.EXIT_NOT_CONFIGURED and not status["ok"]
    assert "UNO_IMAP" in status["error"]
    assert mail_poll.main([]) == 2


def test_mail_error_exit_1_and_failure_counter(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _raise(mail_import.MailError("Nepavyko prisijungti")))
    assert mail_poll.poll_once()[0] == 1
    assert mail_poll.poll_once()[0] == 1
    assert _status(mailenv)["consecutive_failures"] == 2


def test_processing_error_exit_1_without_content(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))

    def boom(n, d=30):
        raise ValueError("slaptas tekstas")

    monkeypatch.setattr(services, "build_mail_result", boom)
    code, status = mail_poll.poll_once()
    assert code == 1 and "slaptas" not in status["error"]


def test_success_after_failure_resets_counter_and_failure_keeps_last_summary(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _raise(mail_import.MailError("x")))
    mail_poll.poll_once()
    failed = _status(mailenv)
    assert failed["consecutive_failures"] == 1 and failed["last_ok_at"] and failed["pdf_count"] == 0
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    assert _status(mailenv)["consecutive_failures"] == 0


def test_loop_survives_failure_and_stops(mailenv, monkeypatch):
    stop = threading.Event()
    codes = iter([1, 0, 0])
    seen = []

    def fake_poll(days):
        seen.append(days)
        if len(seen) == 3:
            stop.set()
        return next(codes), {}

    monkeypatch.setattr(mail_poll, "poll_once", fake_poll)
    monkeypatch.setattr(stop, "wait", lambda s: None)
    monkeypatch.setattr(mail_poll.signal, "signal", lambda *a: None)
    assert mail_poll.run_loop(15, 30, stop) == 0
    assert seen == [30, 30, 30]


def test_healthcheck_fresh_stale_and_failing(mailenv, monkeypatch):
    assert mail_poll.healthy(15) is False  # būsenos dar nėra
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    assert mail_poll.healthy(15) is True
    assert mail_poll.main(["--healthcheck"]) == 0
    later = datetime.now(timezone.utc) + timedelta(minutes=15 * 2 + mail_poll.HEALTH_SLACK_MIN + 1)
    assert mail_poll.healthy(15, now=later) is False
    path = services.mail_status_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["consecutive_failures"] = mail_poll.MAX_FAILURES_HEALTHY
    path.write_text(json.dumps(data), encoding="utf-8")
    assert mail_poll.healthy(15) is False
    assert mail_poll.main(["--healthcheck"]) == 1


# ---------- app sinchronizacija ----------

def test_sync_loads_result_after_worker_poll_and_only_once(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    results: dict = {}
    built = []
    real = services.build_mail_result
    monkeypatch.setattr(services, "build_mail_result", lambda n, d=30: built.append(n) or real(n, d))
    services.sync_mail_invoices(results)
    assert results["invoices"]["_mail_polled"]
    services.sync_mail_invoices(results)
    assert len(built) == 1  # ta pati būsena: be perskaičiavimo


def test_sync_does_not_override_manual_or_demo_result(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    manual = {"summary": "demo"}
    results = {"invoices": manual}
    services.sync_mail_invoices(results)
    assert results["invoices"] is manual


def test_sync_ignores_failed_status(mailenv):
    (mailenv / "mail_status.json").write_text(json.dumps({"ok": False, "finished_at": "2026-01-01T00:00:00+00:00"}))
    results: dict = {}
    services.sync_mail_invoices(results)
    assert results == {}


def test_api_module_invoices_picks_up_worker_result(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    mail_poll.poll_once()
    monkeypatch.setattr(main, "RESULTS", {})
    body = TestClient(main.app).get("/api/module/invoices").json()
    assert body.get("empty") is not True and "kpis" in body


# ---------- Ratas 1 pataisos ----------

def test_s6_processing_error_log_has_type_only(mailenv, monkeypatch, caplog):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))

    def boom(n, d=30):
        raise ValueError("SASKAITA-TEKSTAS-777")

    monkeypatch.setattr(services, "build_mail_result", boom)
    with caplog.at_level("DEBUG"):
        mail_poll.poll_once()
    text = "\n".join(r.getMessage() for r in caplog.records) + caplog.text
    assert "ValueError" in text and "SASKAITA-TEKSTAS-777" not in text


def test_s7_rerun_keeps_mail_markers(mailenv, monkeypatch):
    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", _fake_fetch({}))
    result = services.run_invoices_mail()
    result["_mail_finished_at"] = "2026-11-07T10:00:00+00:00"
    again = services.rerun_invoices(result)
    assert again["_mail_polled"] is True and again["_mail_finished_at"] == "2026-11-07T10:00:00+00:00"


def test_s4_import_serialized_between_threads(mailenv, monkeypatch):
    state = {"now": 0, "max": 0}
    guard = threading.Lock()

    def slow_fetch(dest, *, days=30, limit=50):
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        import time
        time.sleep(0.15)
        (dest / "a.pdf").write_bytes(b"%PDF-1.4 test")
        with guard:
            state["now"] -= 1
        return [{"file": "a.pdf"}]

    monkeypatch.setattr(mail_import, "fetch_invoice_pdfs", slow_fetch)
    monkeypatch.setattr(services, "build_mail_result",
                        lambda n, days=30: {"n": n, "folder_pdfs": len(list(services.mail_folder().glob("*.pdf")))})
    results = []
    threads = [threading.Thread(target=lambda: results.append(services.run_invoices_mail())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1
    assert results == [{"n": 1, "folder_pdfs": 1}] * 2
    assert not list(mailenv.glob("mail_incoming*"))


def test_s4_lock_blocks_other_process(mailenv, monkeypatch):
    import subprocess
    import sys
    import time

    code = ("import time\nfrom app import services\n"
            "with services.mail_import_lock():\n    print('locked', flush=True)\n    time.sleep(3)\n")
    env = {**__import__("os").environ, "UNO_MAIL_DIR": str(mailenv / "mail")}
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert proc.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with services.mail_import_lock(timeout=0.6):
                pass
        assert time.monotonic() - started < 2.5
    finally:
        proc.wait(timeout=10)
    with services.mail_import_lock(timeout=2):  # po proceso pabaigos užraktas laisvas
        pass
