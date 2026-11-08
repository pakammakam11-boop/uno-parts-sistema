"""HR verslo logika be HTTP: likutis, būsenų mašina, idempotentiškas postback, sha256, prieiga, nuorodos, CSRF."""
import hashlib
import json
import threading

import pytest
from sqlalchemy import func, select, update

from core.hr import clock, db, flow, login, notify, people, service
from core.hr.errors import HrError
from core.hr.schema import (hr_audit, hr_documents, hr_employees, hr_login_attempts, hr_marks, hr_outbox,
                            hr_requests, hr_signers)
from hr_testkit import (ANDRIUS, AUSTEJA, BUHALTERE, DIREKTORIUS, DOVILE, FEB_WEEK, JONAS, LUKAS, PARDAVIMU_VADOVAS,
                        SANDELIO_VADOVE, actor_for, hr_env)  # noqa: F401  (hr_env yra fixture)


def leave_data(substitute_id, **extra):
    return {"type": "leave", "leave_kind": "annual", "substitute_id": substitute_id, **FEB_WEEK, **extra}


def new_leave(employee_id=DOVILE, substitute_id=ANDRIUS, **extra) -> int:
    actor = actor_for(employee_id)
    with db.tx() as conn:
        return service.create_request(conn, actor, leave_data(substitute_id, **extra))[0]


def doc_of(request_id, kind="request") -> dict:
    with db.tx() as conn:
        return dict(conn.execute(select(hr_documents).where(hr_documents.c.request_id == request_id,
                                                            hr_documents.c.kind == kind)).mappings().one())


def sign_as(employee_id, request_id, kind="request", action="sign", reason=None):
    doc = doc_of(request_id, kind)
    with db.tx() as conn:
        signer = conn.execute(select(hr_signers).where(
            hr_signers.c.document_id == doc["id"], hr_signers.c.employee_id == employee_id,
            hr_signers.c.status == "pending").order_by(hr_signers.c.sequence)).mappings().first()
        flow.sign(conn, actor_for_conn(conn, employee_id), doc["signing_token"], signer["signer_id"], action, reason)


def actor_for_conn(conn, employee_id):
    return people.make_actor(conn, people.get_employee(conn, employee_id), "csrf-test")


def approve(request_id, chain):
    for emp in chain:
        sign_as(emp, request_id)


def balance(employee_id) -> float:
    with db.tx() as conn:
        return conn.execute(select(hr_employees.c.balance).where(hr_employees.c.id == employee_id)).scalar_one()


def request_row(request_id) -> dict:
    with db.tx() as conn:
        return dict(conn.execute(select(hr_requests).where(hr_requests.c.id == request_id)).mappings().one())


def count(table, *conds) -> int:
    with db.tx() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*conds)).scalar_one()


CHAIN = (DOVILE, ANDRIUS, PARDAVIMU_VADOVAS)


# ---------- validacija ----------

def test_balance_check_message_uses_decimal_comma(hr_env):
    actor = actor_for(JONAS)  # likutis 3,5 d.
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.create_request(conn, actor, leave_data(LUKAS))
    assert e.value.status == 400 and e.value.detail == "Nepakanka likučio: liko 3,5 d."


def test_pending_annual_requests_reserve_balance(hr_env):
    new_leave(DOVILE, ANDRIUS, date_from="2027-03-01", date_to="2027-03-05")  # 5 iš 7 d.
    actor = actor_for(DOVILE)
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.create_request(conn, actor, leave_data(ANDRIUS, date_from="2027-04-05", date_to="2027-04-08"))
    assert e.value.detail == "Nepakanka likučio: liko 2 d."


def test_unpaid_leave_ignores_balance(hr_env):
    request_id = new_leave(JONAS, LUKAS, leave_kind="unpaid")
    assert request_row(request_id)["work_days"] == 5


@pytest.mark.parametrize("patch,status", [
    ({"date_to": "2027-01-29"}, 400),                    # pabaiga < pradžia
    ({"date_from": "2027-02-06", "date_to": "2027-02-07"}, 400),  # tik savaitgalis
    ({"date_from": "2028-02-01", "date_to": "2028-02-02"}, 400),  # už palaikomų metų
    ({"date_from": "ne-data"}, 400),
    ({"leave_kind": "xx"}, 400),
    ({"substitute_id": DOVILE}, 400),                    # pats sau
    ({"substitute_id": PARDAVIMU_VADOVAS}, 400),         # vadovas negali būti pavaduojantis
    ({"substitute_id": 999}, 400),
    ({"substitute_id": None}, 400),
])
def test_validation_rejects(hr_env, patch, status):
    actor = actor_for(DOVILE)
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.create_request(conn, actor, {**leave_data(ANDRIUS), **patch})
    assert e.value.status == status and e.value.detail


def test_trip_requires_destination_and_purpose(hr_env):
    actor = actor_for(JONAS)
    data = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Kaunas", "purpose": ""}
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.create_request(conn, actor, data)
    assert "privalomi" in e.value.detail


def test_overlap_is_conflict_but_cancelled_is_free(hr_env):
    first = new_leave(DOVILE, ANDRIUS)
    actor = actor_for(DOVILE)
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.create_request(conn, actor, leave_data(ANDRIUS, date_from="2027-02-03", date_to="2027-02-10"))
    assert e.value.status == 409
    with db.tx() as conn:
        service.cancel_request(conn, actor, first)
    assert new_leave(DOVILE, ANDRIUS)


def test_past_dates_allowed_with_warning(hr_env):
    actor = actor_for(JONAS)
    with db.tx() as conn:
        _, warnings = service.create_request(conn, actor, {
            "type": "leave", "leave_kind": "unpaid", "substitute_id": LUKAS,
            "date_from": "2026-01-05", "date_to": "2026-01-05"})
    assert warnings


# ---------- būsenų mašina ir pasekmės ----------

def test_full_chain_approves_decrements_balance_and_marks(hr_env):
    request_id = new_leave()
    assert request_row(request_id)["status"] == "pending_signatures" and balance(DOVILE) == 7.0
    approve(request_id, CHAIN)
    assert request_row(request_id)["status"] == "approved"
    assert balance(DOVILE) == 2.0
    assert count(hr_marks, hr_marks.c.employee_id == DOVILE, hr_marks.c.mark == "A") == 5


def test_non_annual_leave_does_not_touch_balance(hr_env):
    request_id = new_leave(DOVILE, ANDRIUS, leave_kind="study")
    approve(request_id, CHAIN)
    assert balance(DOVILE) == 7.0
    assert count(hr_marks, hr_marks.c.mark == "MA") == 5


def test_director_request_is_signed_by_director_as_manager_last(hr_env):
    request_id = new_leave(DIREKTORIUS, SANDELIO_VADOVE)
    doc_id = doc_of(request_id)["id"]
    with db.tx() as conn:
        signers = [dict(r) for r in conn.execute(select(hr_signers).where(
            hr_signers.c.document_id == doc_id).order_by(hr_signers.c.sequence)).mappings()]
    assert [(s["role"], s["employee_id"]) for s in signers] == [
        ("employee", DIREKTORIUS), ("substitute", SANDELIO_VADOVE), ("manager", DIREKTORIUS)]
    approve(request_id, (DIREKTORIUS, SANDELIO_VADOVE))
    assert request_row(request_id)["status"] == "pending_signatures"   # pavaduojantis negali patvirtinti už direktorių
    sign_as(DIREKTORIUS, request_id)
    assert request_row(request_id)["status"] == "approved"


def test_out_of_turn_signing_is_conflict(hr_env):
    request_id = new_leave()
    with pytest.raises(HrError) as e:
        sign_as(PARDAVIMU_VADOVAS, request_id)
    assert e.value.status == 409
    assert request_row(request_id)["status"] == "pending_signatures"


def test_decline_rejects_with_reason_and_blocks_further_signing(hr_env):
    request_id = new_leave()
    sign_as(DOVILE, request_id)
    sign_as(ANDRIUS, request_id, action="decline", reason="Tuo metu atostogauju")
    row = request_row(request_id)
    assert row["status"] == "rejected" and row["reason"] == "Tuo metu atostogauju" and row["declined_by"] == ANDRIUS
    with pytest.raises(HrError):
        sign_as(PARDAVIMU_VADOVAS, request_id)
    assert balance(DOVILE) == 7.0 and count(hr_marks) == 0


def test_decline_requires_reason(hr_env):
    request_id = new_leave()
    with pytest.raises(HrError) as e:
        sign_as(DOVILE, request_id, action="decline", reason=" ")
    assert e.value.status == 400


def test_cancel_only_owner_and_blocks_signing(hr_env):
    request_id = new_leave()
    hr_actor, owner = actor_for(BUHALTERE), actor_for(DOVILE)
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.cancel_request(conn, hr_actor, request_id)  # HR mato, bet atšaukti negali
    assert e.value.status == 403
    with db.tx() as conn:
        service.cancel_request(conn, owner, request_id)
    assert request_row(request_id)["status"] == "cancelled"
    with pytest.raises(HrError):
        sign_as(DOVILE, request_id)


def test_cancel_after_approval_is_conflict(hr_env):
    request_id = new_leave()
    approve(request_id, CHAIN)
    owner = actor_for(DOVILE)
    with pytest.raises(HrError) as e, db.tx() as conn:
        service.cancel_request(conn, owner, request_id)
    assert e.value.status == 409


def test_trip_creates_order_and_marks_after_manager_signs_order(hr_env):
    actor = actor_for(JONAS)
    data = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Ryga", "purpose": "Tiekėjo vizitas"}
    with db.tx() as conn:
        request_id = service.create_request(conn, actor, data)[0]
    approve(request_id, (JONAS, LUKAS, SANDELIO_VADOVE))
    row = request_row(request_id)
    assert row["status"] == "approved" and row["order_status"] == "pending_order"
    assert count(hr_marks) == 0 and balance(JONAS) == 3.5
    sign_as(SANDELIO_VADOVE, request_id, kind="order")
    assert request_row(request_id)["order_status"] == "order_signed"
    assert count(hr_marks, hr_marks.c.mark == "K") == 5


def test_declined_order_rejects_the_request_with_reason_and_actor(hr_env):
    actor = actor_for(JONAS)
    data = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Ryga", "purpose": "Tiekėjo vizitas"}
    with db.tx() as conn:
        request_id = service.create_request(conn, actor, data)[0]
    approve(request_id, (JONAS, LUKAS, SANDELIO_VADOVE))
    sign_as(SANDELIO_VADOVE, request_id, kind="order", action="decline", reason="Nėra biudžeto")
    row = request_row(request_id)
    assert row["status"] == "rejected" and row["order_status"] == "order_rejected"
    assert row["reason"] == "Nėra biudžeto" and row["declined_by"] == SANDELIO_VADOVE
    assert count(hr_marks) == 0
    assert new_leave(JONAS, LUKAS, leave_kind="unpaid")   # datos vėl laisvos


# ---------- lenktynės (lygiagrečios transakcijos) ----------

def run_parallel(*jobs):
    """Paleidžia darbus vienu metu (barjeras); grąžina [(rezultatas, klaida)] ta pačia tvarka."""
    barrier = threading.Barrier(len(jobs))
    out = [None] * len(jobs)

    def worker(i, job):
        barrier.wait()
        try:
            out[i] = (job(), None)
        except HrError as e:
            out[i] = (None, e)

    threads = [threading.Thread(target=worker, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(o is not None for o in out)
    return out


def postback(token, event, signer_id=None):
    def job():
        with db.tx() as conn:
            return flow.handle_postback(conn, token=token, signer_id=signer_id, event=event)
    return job


def mark_all_signed(request_id):
    doc = doc_of(request_id)
    with db.tx() as conn:
        conn.execute(update(hr_signers).where(hr_signers.c.document_id == doc["id"]).values(
            status="signed", signed_at=clock.now(), method="Smart-ID (demo)"))
    return doc


def test_concurrent_duplicate_signing_completed_applies_leave_effects_once(hr_env):
    request_id = new_leave()
    doc = mark_all_signed(request_id)
    results = run_parallel(*[postback(doc["signing_token"], "signing_completed")] * 4)
    assert all(err is None for _, err in results)
    assert sorted(r["changed"] for r, _ in results) == [False, False, False, True]
    assert balance(DOVILE) == 2.0 and count(hr_marks, hr_marks.c.employee_id == DOVILE) == 5
    assert count(hr_audit, hr_audit.c.action == "approved") == 1


def test_concurrent_duplicate_signing_completed_creates_one_trip_order(hr_env):
    actor = actor_for(JONAS)
    data = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Ryga", "purpose": "Tiekėjo vizitas"}
    with db.tx() as conn:
        request_id = service.create_request(conn, actor, data)[0]
    doc = mark_all_signed(request_id)
    results = run_parallel(*[postback(doc["signing_token"], "signing_completed")] * 4)
    assert all(err is None for _, err in results)
    assert count(hr_documents, hr_documents.c.request_id == request_id, hr_documents.c.kind == "order") == 1
    assert count(hr_audit, hr_audit.c.action == "order_created") == 1


def test_cancel_racing_with_final_signature_never_reapproves_cancelled_request(hr_env):
    request_id = new_leave()
    sign_as(DOVILE, request_id)
    sign_as(ANDRIUS, request_id)
    token = doc_of(request_id)["signing_token"]

    def cancel():
        with db.tx() as conn:
            service.cancel_request(conn, actor_for_conn(conn, DOVILE), request_id)

    (_, cancel_err), (_, sign_err) = run_parallel(cancel, postback(token, "signer_signed", "s3"))
    row, doc = request_row(request_id), doc_of(request_id)
    assert (cancel_err is None) != (sign_err is None)           # laimi lygiai vienas
    if cancel_err is None:
        assert row["status"] == "cancelled" and doc["status"] == "cancelled"
        assert balance(DOVILE) == 7.0 and count(hr_marks) == 0 and sign_err.status == 409
    else:
        assert row["status"] == "approved" and doc["status"] == "completed" and cancel_err.status == 409
        assert balance(DOVILE) == 2.0 and count(hr_marks) == 5


def test_concurrent_requests_cannot_overbook_the_balance(hr_env):
    """7 d. likutis, du 5 d. prašymai skirtingoms savaitėms: praeina tik vienas."""
    def create(week):
        def job():
            with db.tx() as conn:
                return service.create_request(conn, actor_for_conn(conn, DOVILE), leave_data(ANDRIUS, **week))[0]
        return job

    results = run_parallel(create(FEB_WEEK), create({"date_from": "2027-02-08", "date_to": "2027-02-12"}))
    assert sum(err is None for _, err in results) == 1
    assert next(err for _, err in results if err).status == 400
    assert count(hr_requests, hr_requests.c.status == "pending_signatures") == 1


# ---------- idempotentiškas postback, sha256 ----------

def test_repeated_postbacks_do_not_double_apply(hr_env):
    request_id = new_leave()
    approve(request_id, CHAIN)
    doc = doc_of(request_id)
    for event, signer in (("signing_completed", "s1"), ("signing_completed", None), ("signer_signed", "s3"),
                          ("signer_signed", "s1"), ("signing_completed", "s2")):
        with db.tx() as conn:
            result = flow.handle_postback(conn, token=doc["signing_token"], signer_id=signer, event=event)
        assert result["changed"] is False
    assert balance(DOVILE) == 2.0
    assert count(hr_marks, hr_marks.c.employee_id == DOVILE) == 5
    assert count(hr_audit, hr_audit.c.action == "approved", hr_audit.c.request_id == request_id) == 1
    assert count(hr_audit, hr_audit.c.action == "balance_decreased") == 1


def test_completed_before_all_signed_is_conflict(hr_env):
    request_id = new_leave()
    sign_as(DOVILE, request_id)
    token = doc_of(request_id)["signing_token"]   # atskira transakcija: SQLite BEGIN IMMEDIATE neleidžia įdėtų
    with pytest.raises(HrError) as e, db.tx() as conn:
        flow.handle_postback(conn, token=token, signer_id=None, event="signing_completed")
    assert e.value.status == 409 and balance(DOVILE) == 7.0


def test_unknown_event_and_token(hr_env):
    with pytest.raises(HrError) as e, db.tx() as conn:
        flow.handle_postback(conn, token="nera", signer_id="s1", event="signer_signed")
    assert e.value.status == 404
    with pytest.raises(HrError) as e, db.tx() as conn:
        flow.handle_postback(conn, token="nera", signer_id="s1", event="kitas")
    assert e.value.status == 400


def test_sha256_is_verified_before_each_signature(hr_env):
    request_id = new_leave()
    sign_as(DOVILE, request_id)
    doc = doc_of(request_id)
    assert doc["sha256"] == hashlib.sha256(bytes(doc["pdf"])).hexdigest()
    with db.tx() as conn:  # kas nors pakeičia PDF DB
        conn.execute(update(hr_documents).where(hr_documents.c.id == doc["id"]).values(pdf=bytes(doc["pdf"]) + b"x"))
    with pytest.raises(HrError) as e:
        sign_as(ANDRIUS, request_id)
    assert e.value.status == 409 and "kontrolinė suma" in e.value.detail
    assert count(hr_audit, hr_audit.c.action == "tamper_detected") == 1   # audito įrašas išsaugotas
    assert count(hr_signers, hr_signers.c.status == "signed") == 1        # Andriaus parašo nėra
    assert request_row(request_id)["status"] == "pending_signatures"


def tamper_payload(request_id, **changes):
    doc = doc_of(request_id)
    payload = json.loads(doc["payload"]) | changes
    with db.tx() as conn:
        conn.execute(update(hr_documents).where(hr_documents.c.id == doc["id"]).values(
            payload=json.dumps(payload, ensure_ascii=False)))


def test_changed_payload_blocks_signing_even_when_pdf_bytes_are_intact(hr_env):
    request_id = new_leave(comment="Originalus komentaras")
    sign_as(DOVILE, request_id)
    tamper_payload(request_id, comment="Pakeistas komentaras")
    with pytest.raises(HrError) as e:
        sign_as(ANDRIUS, request_id)
    assert e.value.status == 409 and "kontrolinė suma" in e.value.detail
    assert count(hr_audit, hr_audit.c.action == "tamper_detected") == 1
    assert count(hr_signers, hr_signers.c.status == "signed") == 1
    assert request_row(request_id)["status"] == "pending_signatures"


def test_changed_payload_blocks_building_the_signed_pdf(hr_env):
    request_id = new_leave(comment="Originalus komentaras")
    doc = mark_all_signed(request_id)
    tamper_payload(request_id, comment="Pakeistas komentaras")
    with pytest.raises(HrError) as e, db.tx() as conn:
        flow.handle_postback(conn, token=doc["signing_token"], signer_id=None, event="signing_completed")
    assert e.value.status == 409
    assert doc_of(request_id)["signed_pdf"] is None and request_row(request_id)["status"] == "pending_signatures"
    assert balance(DOVILE) == 7.0


def test_signed_pdf_has_signature_page(hr_env):
    import io
    import pdfplumber
    request_id = new_leave()
    approve(request_id, CHAIN)
    doc = doc_of(request_id)
    with pdfplumber.open(io.BytesIO(bytes(doc["signed_pdf"]))) as pdf:
        assert len(pdf.pages) == 2
        last = pdf.pages[-1].extract_text()
    assert "Demo parašas (imitacija)" in last and doc["sha256"][:32] in last and "Dovilė Paulauskaitė" in last
    with pdfplumber.open(io.BytesIO(bytes(doc["pdf"]))) as pdf:
        assert len(pdf.pages) == 1


# ---------- prieiga ----------

def test_access_rules(hr_env):
    request_id = new_leave(DOVILE, ANDRIUS)
    row = request_row(request_id)
    cases = {DOVILE: True, ANDRIUS: True, PARDAVIMU_VADOVAS: True, BUHALTERE: True,
             AUSTEJA: False, SANDELIO_VADOVE: False, JONAS: False, DIREKTORIUS: False}
    with db.tx() as conn:
        for employee_id, expected in cases.items():
            assert people.can_view(conn, actor_for_conn(conn, employee_id), row) is expected, employee_id


def test_roles(hr_env):
    assert actor_for(JONAS).roles == ["employee"]
    assert actor_for(SANDELIO_VADOVE).roles == ["employee", "manager"]
    assert actor_for(BUHALTERE).roles == ["employee", "hr"]
    assert actor_for(DIREKTORIUS).is_manager  # direktorius pats sau ne pavaldinys, bet turi pavaldinių


def test_outbox_gets_invites_in_order(hr_env):
    request_id = new_leave()
    sign_as(DOVILE, request_id)
    with db.tx() as conn:
        mails = [(m["to_employee_id"], m["kind"]) for m in conn.execute(select(hr_outbox).order_by(hr_outbox.c.id)).mappings()]
    assert mails == [(ANDRIUS, "sign_invite")]
    sign_as(ANDRIUS, request_id)
    sign_as(PARDAVIMU_VADOVAS, request_id)
    with db.tx() as conn:
        kinds = [(m["to_employee_id"], m["kind"]) for m in conn.execute(select(hr_outbox).order_by(hr_outbox.c.id)).mappings()]
    assert kinds == [(ANDRIUS, "sign_invite"), (PARDAVIMU_VADOVAS, "sign_invite"), (DOVILE, "approved")]


# ---------- vienkartinės nuorodos, sesijos, CSRF ----------

def issue_link(email="jonas@demo.uno.lt", ip="1.1.1.1") -> str:
    with db.tx() as conn:
        login.request_magic_link(conn, email, ip)
        link = conn.execute(select(hr_outbox.c.link).where(hr_outbox.c.kind == "login_link")
                            .order_by(hr_outbox.c.id.desc())).scalar()
    return link.rsplit("/", 1)[1]


def test_magic_link_is_single_use_and_stores_only_hash(hr_env):
    token = issue_link()
    from core.hr.schema import hr_magic_links
    with db.tx() as conn:
        stored = conn.execute(select(hr_magic_links.c.token_hash)).scalar_one()
        assert stored == login.hash_token(token) and token not in stored
        assert login.consume_magic_link(conn, token) == JONAS
    with db.tx() as conn:
        assert login.consume_magic_link(conn, token) is None


def test_magic_link_expires_after_30_minutes(hr_env, monkeypatch):
    token = issue_link()
    real = clock.now()
    monkeypatch.setattr(clock, "now", lambda: real + 31 * 60)
    with db.tx() as conn:
        assert login.consume_magic_link(conn, token) is None


def test_unknown_email_creates_nothing_and_no_error(hr_env):
    with db.tx() as conn:
        login.request_magic_link(conn, "nera@demo.uno.lt", "1.1.1.1")
        login.request_magic_link(conn, "", "1.1.1.1")
    assert count(hr_outbox) == 0


def test_magic_link_request_is_rate_limited(hr_env):
    for _ in range(login.RATE_PER_EMAIL + 3):
        issue_link()
    assert count(hr_outbox, hr_outbox.c.kind == "login_link") == login.RATE_PER_EMAIL
    assert count(hr_login_attempts) > 0                       # bandymai saugomi DB, ne proceso atmintyje


def test_rate_limit_per_ip_and_window_expiry(hr_env, monkeypatch):
    monkeypatch.setattr(login, "RATE_PER_IP", 3)
    emails = ["jonas", "lukas", "rasa", "dovile", "andrius"]
    for name in emails:
        issue_link(f"{name}@demo.uno.lt", ip="9.9.9.9")
    assert count(hr_outbox, hr_outbox.c.kind == "login_link") == 3
    real = clock.now()
    monkeypatch.setattr(clock, "now", lambda: real + login.RATE_WINDOW + 5)
    issue_link("jonas@demo.uno.lt", ip="9.9.9.9")
    assert count(hr_outbox, hr_outbox.c.kind == "login_link") == 4


def test_rate_limit_stores_email_only_as_hash(hr_env):
    issue_link()
    with db.tx() as conn:
        keys = [r[0] for r in conn.execute(select(hr_login_attempts.c.key))]
    assert keys and not any("jonas" in k for k in keys)


def test_destination_and_purpose_limited_to_200_chars(hr_env):
    actor = actor_for(JONAS)
    base = {"type": "trip", "substitute_id": LUKAS, **FEB_WEEK, "destination": "Ryga", "purpose": "Vizitas"}
    for field in ("destination", "purpose"):
        with pytest.raises(HrError) as e, db.tx() as conn:
            service.create_request(conn, actor, {**base, field: "x" * 201})
        assert e.value.status == 400 and "200" in e.value.detail
    with db.tx() as conn:
        assert service.create_request(conn, actor, {**base, "destination": "x" * 200, "purpose": "y" * 200})


def test_session_idle_expiry_and_csrf(hr_env, monkeypatch):
    with db.tx() as conn:
        token, csrf = login.create_session(conn, JONAS)
    with db.tx() as conn:
        actor = login.load_actor(conn, token)
        assert actor.id == JONAS
        login.check_csrf(actor, csrf)
        with pytest.raises(HrError) as e:
            login.check_csrf(actor, "blogas")
        assert e.value.status == 403
    real = clock.now()
    monkeypatch.setattr(clock, "now", lambda: real + 8 * 3600 + 5)
    with db.tx() as conn:
        assert login.load_actor(conn, token) is None


def test_resend_only_for_allowlisted(hr_env, monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: calls.append(kw["json"]) or Resp())
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("HR_MAIL_ALLOWLIST", "jonas@demo.uno.lt")
    issue_link("jonas@demo.uno.lt", "9.9.9.1")
    issue_link("lukas@demo.uno.lt", "9.9.9.2")
    assert [c["to"] for c in calls] == [["jonas@demo.uno.lt"]]
    with db.tx() as conn:
        sent = {m["to_email"]: m["sent_via"] for m in conn.execute(select(hr_outbox)).mappings()}
    assert sent == {"jonas@demo.uno.lt": "resend", "lukas@demo.uno.lt": "outbox"}


def test_no_resend_without_key_or_allowlist(hr_env, monkeypatch):
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **kw: pytest.fail("neturi siųsti"))
    issue_link("jonas@demo.uno.lt")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")      # be allowlist
    issue_link("lukas@demo.uno.lt", "9.9.9.3")


def test_seed_is_idempotent(hr_env):
    from core.hr import seed
    with db.tx() as conn:
        assert seed.ensure_seeded(conn) is False
        assert conn.execute(select(func.count()).select_from(hr_employees)).scalar_one() == 12
        emails = {r[0] for r in conn.execute(select(hr_employees.c.email))}
    assert "jonas@demo.uno.lt" in emails and all(e.endswith("@demo.uno.lt") for e in emails)
    assert all(e.isascii() for e in emails)
