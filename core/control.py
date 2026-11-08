"""Mėnesio uždarymo kontrolė: visų modulių radiniai, laiškų juodraščiai, deklaracijų patikra.

Gauna jau paskaičiuotus modulių rezultatus (app.services run_* dict'us) ir nieko neperskaičiuoja.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.models import Finding, money
from core.world import SALES_INVOICES

# Buhalterės kainoraštis (Mažvydo atsiųstas): eilutė, kaina, ką daro sistema, automatizacijos dalis.
PRICE_LIST = [
    ("payroll", "Darbo užmokesčio apskaita, 57 darb.", 1150,
     "Bruto iš sutarto neto kiekvienam darbuotojui, viršvalandžiai, atostoginiai, ligos 1-2 d. DU lentelė importui (Rivilės DU šablonas derinamas pilote).", "60-70 %", 65),
    ("payroll", "Darbo laiko apskaitos kontrolė", 250,
     "Tabelis sulyginamas su Rivile, Sodros pažymomis ir prašymais. Neatitikimams paruošti klausimai vadovams.", "80 %", 80),
    ("control", "Personalo apskaita, 1-SD / 2-SD", 100,
     "Atostoginių ir ligos skaičiavimas kode. Priėmimas, atleidimas ir 1-SD / 2-SD duomenys: piloto etapas.", "20-30 %", 25),
    ("invoices", "Pirkimo ir pardavimo dokumentai", 100,
     "PDF sąskaitos nuskaitomos, PVM patikra, dublikatai, Rivilė ERP tiekėjų ir eilučių importas. Pardavimo sąskaitos iš šablono (siuntimas klientams: pilotas).", "85-90 %", 88),
    ("bank", "Banko operacijos ir mokėjimai", 150,
     "camt.053 sulyginimas su sąskaitomis, skolos, mokėjimų failas bankui (tvirtina žmogus).", "75 %", 75),
    ("control", "Mėnesinės deklaracijos", 100,
     "Sumų patikra prieš pateikimą: GPM313 ir SAM iš DU, PVM iš sąskaitų. Pateikia žmogus.", "30-40 %", 35),
    ("control", "Kontrolė, trūkstami dokumentai, susirašinėjimas", 150,
     "Trūkstamų sąskaitų sąrašas, laiškų juodraščiai tiekėjams ir vadovams, uždarymo sąrašas.", "60-70 %", 65),
]


@dataclass
class Draft:
    to: str
    subject: str
    body: str


def all_findings(results: dict[str, dict]) -> list[Finding]:
    order = {"error": 0, "warning": 1, "info": 2}
    out = [f for r in results.values() for f in r.get("_findings", [])]
    return sorted(out, key=lambda f: (order.get(f.severity, 3), f.module))


def drafts(results: dict[str, dict]) -> list[Draft]:
    """Laiškai iš radinių siūlomų veiksmų: tiekėjams (bankas) ir vadovams (tabelis)."""
    out: list[Draft] = []
    for f in results.get("bank", {}).get("_findings", []):
        if f.severity in ("error", "warning") and f.suggested_action:
            first = f.suggested_action.splitlines()[0]
            to = first.removeprefix("Kam: ") if first.startswith("Kam: ") else "Tiekėjui"
            out.append(Draft(to, f.title, f.suggested_action))
    for f in results.get("payroll", {}).get("_findings", []):
        if f.severity in ("error", "warning") and f.suggested_action:
            out.append(Draft("Padalinio vadovui", f"{f.title} ({f.ref})", f.suggested_action))
    return out


def declaration_checks(results: dict[str, dict]) -> list[Finding]:
    """Ką deklaruoti šį mėnesį, paskaičiuota iš modulių. Programa deklaracijas generuoja, mes tikrinam sumas."""
    out: list[Finding] = []
    summary = results.get("payroll", {}).get("_summary")
    if summary:
        out.append(Finding("control", "info", "GPM313: pajamų mokestis už spalį",
                           f"Iš DU žiniaraščio: GPM {money(summary.gpm)} €, NPD {money(summary.npd)} €. "
                           "Rivilės GPM313 suma turi sutapti iki cento.", ref="GPM313"))
        sodra = money(summary.sodra_employee + summary.sodra_employer)
        out.append(Finding("control", "info", "SAM: Sodros įmokos už spalį",
                           f"Darbuotojų {money(summary.sodra_employee)} € + darbdavio {money(summary.sodra_employer)} € "
                           f"= {sodra} €.", ref="SAM"))
    inv = results.get("invoices")
    if inv:
        vat_in = _kpi_value(inv, "PVM (paruoštų importui)")
        vat_out = money(sum((i.vat for i in SALES_INVOICES), Decimal(0)))
        out.append(Finding("control", "info", "PVM deklaracija ir i.SAF",
                           f"Pirkimų PVM atskaitai {vat_in} (be sulaikytų sąskaitų, jas išsprendus suma padidės), pardavimų PVM {vat_out} €. "
                           "i.SAF registre turi būti tiek pat pirkimo ir pardavimo sąskaitų.", ref="FR0600"))
    return out


# (kodas, pavadinimas, kam, terminas, iš kurio modulio skaičiuojama)
DECLARATIONS = [
    ("GPM313", "Pajamų mokestis nuo DU", "VMI", "iki 15 d.", "payroll"),
    ("SAM", "Sodros įmokos", "Sodra", "iki 15 d.", "payroll"),
    ("i.SAF", "Sąskaitų faktūrų registras", "VMI", "iki 20 d.", "invoices"),
    ("FR0600", "PVM deklaracija", "VMI", "iki 25 d.", "invoices"),
]


def _declaration_blocker(res: dict | None, module: str) -> str:
    """Kodėl deklaracijos dar negalima pateikti; tuščia eilutė = galima.
    Sąskaitose ir įspėjimai (trūkstami laukai) sulaiko sąskaitą, todėl PVM suma dar keisis."""
    if not res:
        return "šaltinis dar nepaskaičiuotas"
    c = res.get("counts", {})
    open_ = c.get("error", 0) + (c.get("warning", 0) if module == "invoices" else 0)
    return f"{open_} neišspręst. radinių modulyje" if open_ else ""


def declarations(results: dict[str, dict]) -> dict:
    """Mėnesinių deklaracijų puslapis: sumos, terminai ir ar jas jau galima pateikti."""
    found: list[Finding] = declaration_checks(results)
    checks = {f.ref: f for f in found}
    checks["i.SAF"] = checks.get("FR0600")
    rows = []
    for code, title, to, due, module in DECLARATIONS:
        blocker = _declaration_blocker(results.get(module), module)
        rows.append({"code": code, "title": title, "to": to, "due": due,
                     "status": f"Laukia: {blocker}" if blocker else "Paruošta pateikti",
                     "detail": checks[code].detail if checks.get(code) else "",
                     "_sev": "warning" if blocker else ""})
        if blocker:
            found.append(Finding("control", "warning", f"{code}: dar negalima pateikti", f"{blocker}.",
                                 ref=code, suggested_action="Išspręsti modulio radinius ir dar kartą patikrinti sumas."))
    ready = sum(1 for r in rows if not r["_sev"])
    return {
        "kpis": [{"label": "Deklaracijos šį mėnesį", "value": str(len(rows))},
                 {"label": "Paruošta pateikti", "value": f"{ready} iš {len(rows)}"},
                 {"label": "Artimiausias terminas", "value": "lapkričio 15 d."}],
        "findings": found,
        "table": {"title": "Spalio deklaracijos", "columns": [
            {"key": "code", "label": "Deklaracija"}, {"key": "title", "label": "Kas"},
            {"key": "to", "label": "Kam"}, {"key": "due", "label": "Terminas"},
            {"key": "status", "label": "Būsena"}, {"key": "detail", "label": "Sumos", "wrap": True}],
            "rows": rows},
        "counts": {"error": sum(f.severity == "error" for f in found),
                   "warning": sum(f.severity == "warning" for f in found)},
    }


def _kpi_value(result: dict, label: str) -> str:
    return next((k["value"] for k in result.get("kpis", []) if k["label"] == label), "")


def checklist(results: dict[str, dict]) -> list[dict]:
    inv, bnk, pay = results.get("invoices"), results.get("bank"), results.get("payroll")

    def item(label: str, res: dict | None, detail_ok: str) -> dict:
        if not res:
            return {"label": label, "done": False, "detail": "Dar nepaleista"}
        c = res["counts"]
        open_ = c["error"] + c["warning"]
        return {"label": label, "done": open_ == 0,
                "detail": detail_ok if open_ == 0 else f"Paskaičiuota, liko išspręsti: {open_}"}

    return [
        item("Pirkimo sąskaitos užregistruotos", inv, "Visos sąskaitos be klaidų"),
        item("Bankas sulygintas su sąskaitomis", bnk, "Visos operacijos sulygintos"),
        item("Tabelis sutikrintas", pay, "Neatitikimų nėra"),
        {"label": "DU paskaičiuotas ir paruoštas importui", "done": bool(pay),
         "detail": "Žiniaraštis paruoštas" if pay else "Dar nepaleista"},
        {"label": "Deklaracijų sumos patikrintos", "done": bool(pay and inv),
         "detail": "GPM313, SAM ir PVM sumos paruoštos" if pay and inv else "Reikia DU ir sąskaitų"},
        {"label": "Mokėjimai patvirtinti banke", "done": False, "detail": "Žmogaus veiksmas"},
    ]
