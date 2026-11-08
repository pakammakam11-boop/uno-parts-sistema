"""Jungčių sluoksnis: kiekvienas šaltinis turi `Connector` (name, status(), fetch(period, branch)).

Demo režime kiekvieną šaltinį atstoja demo jungtis (skaito dash_ faktų lenteles). Tikros jungtys (stub'ai žemiau)
pakeičiamos po vieną, o rodikliai ir UI nesikeičia. Būsenos ir tekstai paimti iš RESEARCH.md lentelės.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select

from core.dashboard import generator as gen
from core.dashboard import periods, queries
from core.hr import clock, people, views
from core.hr.schema import hr_requests

DIRECTOR_ID = 1
FORECAST_LOOKBACK_DAYS = 63
STATUS_LABELS = {"demo": "Demo duomenys", "connected": "Prijungta", "waiting": "Laukia prieigos"}


class ConnectorNotReady(Exception):
    """Tikra jungtis dar neprijungta (laukiama prieigos)."""


class Connector(Protocol):
    id: str
    name: str

    def status(self) -> dict: ...

    def fetch(self, period: str, branch: str) -> dict: ...


# id, pavadinimas, ką teikia, tikros jungties būsenos tekstas, ką reikia atrakinti, iš ko
SOURCES = [
    ("rivile", "Rivilė", ["Kaštai pagal grupes ir dokumentus", "DU ir Sodra", "Depozitai"],
     "Laukia: read-only SQL view",
     "Reikia read-only SQL paskyros su view (I06_PARH, I07_PARD, I07_SAVIKAINA, I02_DKH/I03_DKD, N08, N17) arba "
     "Rivilė API v2 (GAMA nuo 119 versijos). Gamyboje tik read-only view, ne db_owner.",
     "UNO IT / Rivilės administratorius"),
    ("intercars", "Intercars", ["Pardavimai ir marža", "Gamintojai", "Grąžinimai", "Maršrutai", "Intercars mokesčiai"],
     "Laukia leidimo",
     "Intercars IC API: laukia leidimo, kreiptis icapi@intercars.eu (OAuth2 client credentials). Franšizės pardavimų "
     "ir padalinių pelno API viešai neįrodytas: reikia Intercars Navision / 360 BI eksporto.",
     "Intercars Lietuva (icapi@intercars.eu)"),
    ("bankas", "Bankas", ["Kuro kortelių ir kitų kaštų apmokėjimai"], "Laukia prieigos",
     "Paprasčiausia: banko išrašo failo (camt.053) įkėlimas, jis jau veikia apskaitoje. Vėliau banko gateway "
     "(SEB, Swedbank) arba Enable Banking.",
     "Bankas / buhalterė (išrašų įkėlimas)"),
    ("telefonija", "Telefonija", ["Praleisti skambučiai"], "Laukia prieigos",
     "Reikia sužinoti, kokia PBX. Tele2 Mobili stotelė: REST + webhook LostCallerAdded; Telia praleisti "
     "skambučiai tik el. paštu.",
     "Telefonijos tiekėjas"),
    ("pastas", "Paštas", ["Neatsakyti laiškai", "Užduotys"], "Laukia leidimo (Outlook)",
     "Gmail galima prijungti per IMAP iškart; Outlook @intercars.eu per Microsoft Graph, reikia Intercars IT leidimo.",
     "Intercars IT"),
    ("personalas", "Personalas", ["Prašymai, laukiantys parašo", "Kas atostogose"], "Prijungta",
     "Nieko: naudojamas personalo modulis (/hr).", "Mes"),
    ("apskaita", "Apskaita", ["Sulaikytos sąskaitos", "Mėnesio uždarymas"], "Laukia prieigos",
     "Gamyboje bus imami apskaitos modulio rezultatai su tikrais UNO duomenimis (šiuo metu išgalvoti faktai).",
     "Mes (po tikrų duomenų prijungimo)"),
]
SOURCE_BY_ID = {s[0]: s for s in SOURCES}


def _status(source_id: str, status: str, as_of: str) -> dict:
    _, name, provides, real_label, needs, owner = SOURCE_BY_ID[source_id]
    return {"id": source_id, "name": name, "status": status, "status_label": STATUS_LABELS[status], "as_of": as_of,
            "provides": provides, "needs": needs, "from": owner,
            "real": {"status": "connected" if real_label == "Prijungta" else "waiting", "label": real_label}}


class PendingConnector:
    """Tikros jungties stub'as: aiškus būsenos tekstas, fetch neleidžiamas."""

    def __init__(self, source_id: str):
        self.id = source_id
        self.name = SOURCE_BY_ID[source_id][1]

    def status(self) -> dict:
        info = _status(self.id, "waiting", "")
        return {**info, "status_label": SOURCE_BY_ID[self.id][3], "as_of": None}

    def fetch(self, period: str, branch: str) -> dict:
        raise ConnectorNotReady(SOURCE_BY_ID[self.id][3])


class DemoConnector:
    """Bendras demo jungties pagrindas: laiko DB jungtį ir sako, iki kada duomenys."""
    id = ""
    live = False     # True: duomenys „šiandienos“ (paštas, personalas), ne iki vakar

    def __init__(self, conn):
        self.conn = conn
        self.name = SOURCE_BY_ID[self.id][1]

    def as_of(self) -> str:
        if self.live:
            return clock.iso(clock.now())
        return periods.as_of_day().isoformat()

    def status(self) -> dict:
        return _status(self.id, "demo", self.as_of())


class IntercarsDemo(DemoConnector):
    id = "intercars"

    def fetch(self, period: str, branch: str) -> dict:
        as_of = periods.as_of_day()
        rng = periods.resolve(period, as_of)
        prev = periods.previous(period, rng)
        c = self.conn
        return {
            "as_of": as_of, "range": rng, "previous_range": prev,
            "current": queries.sales_totals(c, rng.start, rng.end, branch),
            "previous": queries.sales_totals(c, prev.start, prev.end, branch),
            "daily": queries.sales_daily(c, min(rng.start, as_of - timedelta(days=FORECAST_LOOKBACK_DAYS - 1)), as_of, branch),
            "by_manufacturer": queries.sales_by(c, "manufacturer", rng.start, rng.end, branch),
            "by_branch": queries.sales_by(c, "branch", rng.start, rng.end, "all"),
            "routes": queries.ops_daily(c, rng.start, rng.end, branch),
            "ic_fees": queries.cost_amount(c, rng.start, rng.end, branch, group="ic_fees"),
        }


class RivileDemo(DemoConnector):
    id = "rivile"

    def fetch(self, period: str, branch: str) -> dict:
        as_of = periods.as_of_day()
        rng = periods.resolve(period, as_of)
        prev = periods.previous(period, rng)
        return {"as_of": as_of, "range": rng, "previous_range": prev,
                "groups": queries.cost_by_group(self.conn, rng.start, rng.end, branch),
                "previous_groups": queries.cost_by_group(self.conn, prev.start, prev.end, branch),
                "sources": queries.cost_sources(self.conn, rng.start, rng.end, branch),
                "deposits": queries.deposits(self.conn, branch)}


class BankDemo(DemoConnector):
    id = "bankas"

    def fetch(self, period: str, branch: str) -> dict:
        rng = periods.resolve(period, periods.as_of_day())
        return {"range": rng, "card_payments": queries.cost_amount(self.conn, rng.start, rng.end, branch, source="Bankas")}


class TelephonyDemo(DemoConnector):
    id = "telefonija"

    def fetch(self, period: str, branch: str) -> dict:
        as_of = periods.as_of_day()
        rng = periods.resolve(period, as_of)
        last = periods.last_trading_day(as_of)
        return {"as_of": as_of, "range": rng, "series": queries.ops_daily(self.conn, rng.start, rng.end, branch),
                "last_day": queries.ops_daily(self.conn, last, last, branch)}


class MailDemo(DemoConnector):
    id = "pastas"
    live = True

    def fetch(self, period: str, branch: str) -> dict:
        now = periods.now_local()
        items = queries.work_items(self.conn)
        mails = [{**i, "waiting_hours": int((now - i["received_at"]).total_seconds() // 3600)}
                 for i in items if i["kind"] == "mail"]
        tasks = [{**i, "days_left": (i["due_day"] - now.date()).days} for i in items if i["kind"] == "task"]
        return {"now": now, "mails": mails, "tasks": tasks}


class HrDemo(DemoConnector):
    """Tikras personalo modulis (core/hr): laukiantys direktoriaus parašo ir šios savaitės atostogos."""
    id = "personalas"
    live = True

    def status(self) -> dict:
        return _status(self.id, "connected", self.as_of())

    def fetch(self, period: str, branch: str) -> dict:
        today = clock.today()
        director = people.get_employee(self.conn, DIRECTOR_ID)
        to_sign = views.list_requests(self.conn, people.make_actor(self.conn, director, ""), "to_sign")
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        names = people.employees_map(self.conn)
        rows = self.conn.execute(select(hr_requests).where(
            hr_requests.c.type == "leave", hr_requests.c.status == "approved",
            hr_requests.c.date_from <= sunday, hr_requests.c.date_to >= monday)).mappings().all()
        pending_total = self.conn.execute(select(func.count()).select_from(hr_requests)
                                          .where(hr_requests.c.status == "pending_signatures")).scalar()
        return {"pending_for_director": [{"id": r["id"], "number": r["number"], "employee": r["employee"]["name"],
                                          "date_from": r["date_from"], "date_to": r["date_to"],
                                          "work_days": r["work_days"], "type": r["type"]} for r in to_sign],
                "pending_total": int(pending_total or 0),
                "on_leave_this_week": [{"name": names[r["employee_id"]]["name"], "date_from": r["date_from"].isoformat(),
                                        "date_to": r["date_to"].isoformat()} for r in rows if r["employee_id"] in names],
                "week": {"from": monday.isoformat(), "to": sunday.isoformat()}, "link": "/hr"}


class AccountingDemo(DemoConnector):
    id = "apskaita"
    live = True

    def fetch(self, period: str, branch: str) -> dict:
        return dict(gen.ACCOUNTING_FACTS)


DEMO_CLASSES = (RivileDemo, IntercarsDemo, BankDemo, TelephonyDemo, MailDemo, HrDemo, AccountingDemo)


def demo_connectors(conn) -> dict[str, Connector]:
    return {cls.id: cls(conn) for cls in DEMO_CLASSES}


def real_connectors() -> dict[str, Connector]:
    """Tikros jungtys: dabar visos stub'ai (personalas jau prijungtas per core/hr)."""
    return {s[0]: PendingConnector(s[0]) for s in SOURCES}


def describe_sources(conn) -> list[dict]:
    """Šaltinių sąrašas /sources: aktyvi (demo) jungtis ir tikros jungties būsena."""
    active = demo_connectors(conn)
    out = []
    for s in SOURCES:
        info = active[s[0]].status()
        out.append({**info, "covers": ", ".join(info["provides"]), "todo": info["needs"], "contact": info["from"]})
    return out


def overall_status(source_ids: list[str], conn) -> tuple[str, str]:
    """Atsakymo būsena iš panaudotų šaltinių: demo, jei bent vienas demo; kitaip prijungta."""
    active = demo_connectors(conn)
    states = {active[i].status()["status"] for i in source_ids}
    status = "demo" if "demo" in states else ("waiting" if "waiting" in states else "connected")
    return status, STATUS_LABELS[status]


def source_names(source_ids: list[str]) -> str:
    return " + ".join(SOURCE_BY_ID[i][1] for i in source_ids)


def envelope(conn, source_ids: list[str]) -> dict:
    """Bendri kiekvieno atsakymo laukai: source, as_of, status. as_of imamas iš seniausio panaudoto šaltinio."""
    active = demo_connectors(conn)
    as_ofs = sorted(active[i].as_of() for i in source_ids)
    status, label = overall_status(source_ids, conn)
    return {"source": source_names(source_ids), "as_of": as_ofs[0], "status": status, "status_label": label}
