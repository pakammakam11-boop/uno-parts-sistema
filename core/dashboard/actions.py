"""„Šiandien reikia tavo sprendimo“: veiksmų eilė, sudaroma taisyklėmis iš faktų (be AI, be spėliojimo)."""
from __future__ import annotations

from datetime import timedelta

from core.dashboard import connectors, queries
from core.dashboard import periods as per
from core.dashboard.generator import FUEL_SUPPLIERS, WEIGHTED_AOV

FUEL_SPIKE_PCT = 10.0          # kuras vs 3 ankstesnių 30 d. langų vidurkis
FUEL_WINDOW_DAYS = 30
DEPOSIT_OLD_DAYS = 30
MISSED_CALLS_ALERT = 15
CALL_CONVERSION = 0.35         # skambutis -> užsakymas (prielaida, rodoma ekrane)
MAIL_WAIT_ALERT_HOURS = 8
BUDGET_SHORTFALL_PCT = 95.0
MAX_ACTIONS = 6
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def eur_text(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " €"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return many if n % 10 == 0 or 11 <= n % 100 <= 19 else few


def avg_order_value(daily: list[dict], days: int = 28) -> float:
    """Vidutinis užsakymas iš paskutinių `days` dienų sumų (pardavimai / užsakymai)."""
    recent = daily[-days:]
    orders = sum(r["orders"] for r in recent)
    return sum(r["revenue"] for r in recent) / orders if orders else WEIGHTED_AOV


def lost_revenue(missed: int, avg_order: float) -> float:
    return missed * CALL_CONVERSION * avg_order


def _fuel_facts(conn, branch: str, as_of) -> dict:
    windows = []
    for i in range(4):
        end = as_of - timedelta(days=FUEL_WINDOW_DAYS * i)
        start = end - timedelta(days=FUEL_WINDOW_DAYS - 1)
        windows.append(queries.cost_amount(conn, start, end, branch, group="transport", suppliers=tuple(FUEL_SUPPLIERS)))
    return {"last30": windows[0], "baseline": sum(windows[1:]) / 3}


def gather(conn, branch: str, forecast: dict | None = None) -> dict:
    """Faktai taisyklėms: viskas iš jungčių / faktų lentelių."""
    c = connectors.demo_connectors(conn)
    as_of = per.as_of_day()
    today = per.now_local().date()
    tel, inter = c["telefonija"].fetch("day", branch), c["intercars"].fetch("week", branch)
    old = [d for d in queries.deposits(conn, branch) if (today - d["created_day"]).days > DEPOSIT_OLD_DAYS]
    last = tel["last_day"][0] if tel["last_day"] else {"day": as_of, "missed_calls": 0, "calls_total": 0}
    mails = [m for m in c["pastas"].fetch("day", branch)["mails"] if m["priority"] == "high"]
    return {
        "as_of": as_of, "fuel": _fuel_facts(conn, branch, as_of),
        "deposits": {"count": len(old), "value": sum(d["value"] for d in old)},
        "calls": {"day": last["day"], "missed": last["missed_calls"],
                  "avg_order": avg_order_value(inter["daily"])},
        "hr_pending": len(c["personalas"].fetch("day", branch)["pending_for_director"]),
        "mail": max(mails, key=lambda m: m["waiting_hours"], default=None),
        "forecast_pct_of_budget": (forecast or {}).get("forecast_pct_of_budget"),
    }


SOURCES = {"fuel": "Rivilė + Bankas", "deposits": "Rivilė", "calls": "Telefonija", "hr": "Personalas",
           "mail": "Paštas", "budget": "Intercars"}


def _action(kind: str, severity: str, title: str, reason: str, link: str, section: str, amount: float | None = None) -> dict:
    return {"id": kind, "kind": kind, "severity": severity, "title": title, "reason": reason, "link": link,
            "section": section, "source": SOURCES[kind], "amount": round(amount, 2) if amount is not None else None}


def _fuel(f: dict) -> list[dict]:
    last, base = f["fuel"]["last30"], f["fuel"]["baseline"]
    pct = per.pct_change(last, base)
    if pct is None or pct < FUEL_SPIKE_PCT:
        return []
    return [_action("fuel", "high" if pct >= 15 else "medium", f"Kuras +{pct:.0f} % vs 3 mėn. vidurkis",
                    f"Paskutinių 30 d. kuro išlaidos {eur_text(last)} vs {eur_text(base)} vidutiniškai per 30 d. "
                    f"ankstesnius 3 mėn.", "#kastai/transport", "costs", last - base)]


def _deposits(f: dict) -> list[dict]:
    n, value = f["deposits"]["count"], f["deposits"]["value"]
    if n == 0:
        return []
    return [_action("deposits", "medium", f"{n} {plural(n, 'depozitas laukia', 'depozitai laukia', 'depozitų laukia')} "
                    f">{DEPOSIT_OLD_DAYS} d., {eur_text(value)}",
                    "Užstatas už senų detalių korpusus neatgautas ilgiau nei 30 dienų.", "#operacijos", "operations", value)]


def _calls(f: dict) -> list[dict]:
    c = f["calls"]
    if c["missed"] < MISSED_CALLS_ALERT:
        return []
    lost = lost_revenue(c["missed"], c["avg_order"])
    when = "vakar" if c["day"] == f["as_of"] else f"{c['day'].isoformat()}"
    return [_action("calls", "high" if lost >= 2000 else "medium",
                    f"Praleista {c['missed']} skambučių {when}, ~{eur_text(lost)} apyvartos",
                    f"Prielaida: {c['missed']} skambučiai x {CALL_CONVERSION * 100:.0f} % konversija x vidutinis "
                    f"užsakymas {eur_text(c['avg_order'])}.", "#operacijos", "operations", lost)]


def _hr(f: dict) -> list[dict]:
    n = f["hr_pending"]
    if n == 0:
        return []
    return [_action("hr", "medium", f"{n} personalo {plural(n, 'prašymas laukia', 'prašymai laukia', 'prašymų laukia')} tavo parašo",
                    "Atostogų ir komandiruočių prašymai, kuriuos turi pasirašyti direktorius.", "/hr", "hr")]


def _mail(f: dict) -> list[dict]:
    m = f["mail"]
    if m is None or m["waiting_hours"] < MAIL_WAIT_ALERT_HOURS:
        return []
    return [_action("mail", "high" if m["waiting_hours"] >= 24 else "medium",
                    f"Neatsakytas {m['counterparty']} laiškas {m['waiting_hours']} val.", f"Tema: {m['title']}",
                    "#darbai", "work")]


def _budget(f: dict) -> list[dict]:
    pct = f["forecast_pct_of_budget"]
    if pct is None or pct >= BUDGET_SHORTFALL_PCT:
        return []
    return [_action("budget", "medium", f"Mėnesio prognozė {100 - pct:.0f} % žemiau biudžeto",
                    f"Prognozuojama apyvarta sudaro {pct:.0f} % mėnesio biudžeto.", "#finansai", "finance")]


RULES = (_fuel, _deposits, _calls, _hr, _mail, _budget)


def rules(facts: dict) -> list[dict]:
    """Taisyklės -> veiksmai, surikiuoti pagal svarbą ir sumą, ne daugiau nei MAX_ACTIONS."""
    found = [a for rule in RULES for a in rule(facts)]
    found.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], -(a["amount"] or 0)))
    return found[:MAX_ACTIONS]


def build(conn, branch: str, forecast: dict | None = None) -> list[dict]:
    return rules(gather(conn, branch, forecast))
