import sqlite3

import pytest

from core.approval import approve, is_approved, record_run, revoke
from core.store import Store

P, M = "2026-10", "payroll"


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path / "uno.db")
    cid = store.add_company("A")
    up = store.save_upload(cid, P, "timesheet", "t.xlsx", b"v1", "ana")
    run = record_run(store, cid, P, M, "1", [up["id"]], {"ok": True}, "ana")
    return store, cid, up, run


def test_approval_valid_then_invalidated_by_new_upload(ctx):
    store, cid, up, run = ctx
    assert not is_approved(store, cid, P, M)
    approve(store, cid, P, M, run, "boss")
    assert is_approved(store, cid, P, M)
    store.save_upload(cid, P, "timesheet", "t2.xlsx", b"v2", "ana")
    assert not is_approved(store, cid, P, M)


def test_new_run_on_new_upload_needs_new_approval(ctx):
    store, cid, up, run = ctx
    approve(store, cid, P, M, run, "boss")
    up2 = store.save_upload(cid, P, "timesheet", "t2.xlsx", b"v2", "ana")
    run2 = record_run(store, cid, P, M, "1", [up2["id"]], {"ok": True}, "ana")
    assert not is_approved(store, cid, P, M)
    approve(store, cid, P, M, run2, "boss")
    assert is_approved(store, cid, P, M)


def test_hash_mismatch_invalidates(ctx):
    store, cid, up, run = ctx
    approve(store, cid, P, M, run, "boss")
    con = sqlite3.connect(store.db_path)
    with con:
        con.execute("UPDATE uploads SET sha256='tampered' WHERE id=?", (up["id"],))
    con.close()
    assert not is_approved(store, cid, P, M)


def test_revoke_and_audit(ctx):
    store, cid, up, run = ctx
    approve(store, cid, P, M, run, "boss")
    assert revoke(store, cid, P, M, "boss") == 1
    assert not is_approved(store, cid, P, M)
    actions = [a["action"] for a in store.list_audit(cid)]
    assert actions == ["upload", "run", "approve", "revoke"]


def test_approval_is_tenant_scoped(ctx):
    store, cid, up, run = ctx
    other = store.add_company("B")
    with pytest.raises(ValueError):
        approve(store, other, P, M, run, "boss")
    approve(store, cid, P, M, run, "boss")
    assert not is_approved(store, other, P, M)


# ---------- audito pataisos ----------

def _timesheet_run(tmp_path):
    store = Store(tmp_path / "uno.db")
    cid = store.add_company("A")
    ids = [store.save_upload(cid, P, k, f"{k}.xlsx", k.encode(), "ana")["id"] for k in ("employees", "timesheet")]
    run = record_run(store, cid, P, "timesheet", "1", ids, {"ok": True}, "ana")
    return store, cid, run


def test_first_upload_of_unused_optional_kind_invalidates(tmp_path):
    store, cid, run = _timesheet_run(tmp_path)
    approve(store, cid, P, "timesheet", run, "boss", "1")
    assert is_approved(store, cid, P, "timesheet", "1")
    store.save_upload(cid, P, "sick_certs", "s.csv", b"x", "ana")
    assert not is_approved(store, cid, P, "timesheet", "1")


def test_rules_version_change_invalidates(tmp_path):
    store, cid, run = _timesheet_run(tmp_path)
    approve(store, cid, P, "timesheet", run, "boss", "1")
    assert not is_approved(store, cid, P, "timesheet", "2")


def test_stale_run_cannot_be_approved_and_leaves_no_row(tmp_path):
    from core.approval import StaleRunError
    store, cid, run = _timesheet_run(tmp_path)
    store.save_upload(cid, P, "vacation_requests", "v.csv", b"x", "ana")
    with pytest.raises(StaleRunError):
        approve(store, cid, P, "timesheet", run, "boss", "1")
    assert store.active_approval(cid, P, "timesheet") is None
    assert "approve" not in [a["action"] for a in store.list_audit(cid)]
