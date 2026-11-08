"""Veiksmų eilės taisyklės: kiekviena taisyklė su savo slenksčiu, rikiavimas, riba, HR integracija."""
from datetime import date

import pytest

from core.dashboard import actions, store
from core.hr import db
from dash_testkit import freeze
from hr_testkit import DIREKTORIUS, JONAS, SANDELIO_VADOVE, Session, create_leave, hr_env, next_sign_url  # noqa: F401


def facts(**over) -> dict:
    base = {"as_of": date(2026, 11, 6), "fuel": {"last30": 10000.0, "baseline": 10000.0},
            "deposits": {"count": 0, "value": 0.0},
            "calls": {"day": date(2026, 11, 6), "missed": 5, "avg_order": 175.0}, "hr_pending": 0, "mail": None,
            "forecast_pct_of_budget": 100.0}
    base.update(over)
    return base


def test_quiet_company_has_no_actions():
    assert actions.rules(facts()) == []


def test_fuel_spike_rule_and_threshold():
    hit = actions.rules(facts(fuel={"last30": 11800.0, "baseline": 10000.0}))
    assert [a["kind"] for a in hit] == ["fuel"] and hit[0]["title"] == "Kuras +18 % vs 3 mėn. vidurkis"
    assert hit[0]["amount"] == 1800.0 and hit[0]["severity"] == "high"
    assert actions.rules(facts(fuel={"last30": 10900.0, "baseline": 10000.0})) == []


def test_old_deposits_rule():
    a = actions.rules(facts(deposits={"count": 12, "value": 1840.0}))[0]
    assert a["kind"] == "deposits" and a["title"] == "12 depozitų laukia >30 d., 1 840 €"
    assert actions.rules(facts(deposits={"count": 1, "value": 50.0}))[0]["title"].startswith("1 depozitas laukia")


def test_missed_calls_rule_uses_assumptions():
    a = actions.rules(facts(calls={"day": date(2026, 11, 6), "missed": 23, "avg_order": 175.0}))[0]
    assert a["kind"] == "calls" and a["title"].startswith("Praleista 23 skambučių vakar, ~1 409 €")
    assert "35 % konversija" in a["reason"] and abs(a["amount"] - 23 * 0.35 * 175) < 0.01
    older = actions.rules(facts(calls={"day": date(2026, 11, 3), "missed": 20, "avg_order": 175.0}))[0]
    assert "2026-11-03" in older["title"]
    assert actions.rules(facts(calls={"day": date(2026, 11, 6), "missed": 14, "avg_order": 175.0})) == []


def test_hr_pending_rule_plural():
    assert actions.rules(facts(hr_pending=1))[0]["title"] == "1 personalo prašymas laukia tavo parašo"
    assert actions.rules(facts(hr_pending=2))[0]["title"] == "2 personalo prašymai laukia tavo parašo"
    assert actions.rules(facts(hr_pending=11))[0]["title"] == "11 personalo prašymų laukia tavo parašo"
    assert actions.rules(facts(hr_pending=2))[0]["link"] == "/hr"


def test_oldest_priority_mail_rule():
    mail = {"counterparty": "Inter Cars Lietuva", "waiting_hours": 26, "title": "Dėl bonusų"}
    a = actions.rules(facts(mail=mail))[0]
    assert a["title"] == "Neatsakytas Inter Cars Lietuva laiškas 26 val." and a["severity"] == "high"
    assert actions.rules(facts(mail={**mail, "waiting_hours": 3})) == []
    assert a["link"] == "#darbai"


def test_budget_shortfall_rule():
    a = actions.rules(facts(forecast_pct_of_budget=91.0))[0]
    assert a["kind"] == "budget" and a["title"] == "Mėnesio prognozė 9 % žemiau biudžeto"
    assert a["link"] == "#finansai"
    assert actions.rules(facts(forecast_pct_of_budget=96.0)) == []


def test_sorted_by_severity_then_amount_and_capped():
    everything = facts(fuel={"last30": 12000.0, "baseline": 10000.0}, deposits={"count": 3, "value": 900.0},
                       calls={"day": date(2026, 11, 6), "missed": 30, "avg_order": 200.0}, hr_pending=2,
                       mail={"counterparty": "X", "waiting_hours": 30, "title": "t"}, forecast_pct_of_budget=80.0)
    out = actions.rules(everything)
    assert len(out) == actions.MAX_ACTIONS == 6
    severities = [a["severity"] for a in out]
    assert severities == sorted(severities, key=lambda s: actions.SEVERITY_ORDER[s])
    highs = [a for a in out if a["severity"] == "high"]
    assert [a["amount"] or 0 for a in highs] == sorted((a["amount"] or 0 for a in highs), reverse=True)


# ---------- iš tikrų faktų (DB + HR) ----------

@pytest.fixture
def seeded(hr_env, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 7))
    store.ready()


def test_demo_data_gives_three_to_six_actions(seeded):
    with db.tx() as conn:
        out = actions.build(conn, "all")
    kinds = {a["kind"] for a in out}
    assert 3 <= len(out) <= 6 and {"fuel", "deposits", "calls", "mail"} <= kinds
    fuel = next(a for a in out if a["kind"] == "fuel")
    assert fuel["title"].startswith("Kuras +1") and fuel["title"].endswith("% vs 3 mėn. vidurkis")
    mail = next(a for a in out if a["kind"] == "mail")
    assert mail["title"] == "Neatsakytas Inter Cars Lietuva laiškas 26 val."
    calls = next(a for a in out if a["kind"] == "calls")
    assert "Praleista 23 skambučių vakar" in calls["title"]


def test_branch_filter_changes_call_facts(seeded):
    with db.tx() as conn:
        vln = actions.gather(conn, "vln")["calls"]["missed"]
        kns = actions.gather(conn, "kns")["calls"]["missed"]
    assert (vln, kns) == (14, 9)


def test_hr_pending_signature_for_director_appears(seeded):
    rasa = Session(SANDELIO_VADOVE)
    rid = create_leave(rasa, JONAS).json()["id"]
    assert rasa.sign(next_sign_url(rasa, rid)).status_code == 200
    jonas = Session(JONAS)
    assert jonas.sign(next_sign_url(jonas, rid)).status_code == 200
    with db.tx() as conn:
        out = actions.build(conn, "all")
    hr = next(a for a in out if a["kind"] == "hr")
    assert hr["title"] == "1 personalo prašymas laukia tavo parašo"
    director = Session(DIREKTORIUS)
    assert director.get("/requests?scope=to_sign").json()[0]["id"] == rid
