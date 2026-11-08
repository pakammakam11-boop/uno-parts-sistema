"""Modulių paleidimas ir rezultatų pavertimas UI JSON'u.

Kiekviena run_* funkcija grąžina dict: kpis, findings, table, downloads, raw (vidiniam naudojimui).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from core import bank, invoices, payroll, rivile_export, rivile_gama_eip, timesheet
from core.models import Finding, Invoice, InvoiceLine
from core.world import EMPLOYEES, PERIOD_END, PERIOD_MONTH, PERIOD_YEAR, PURCHASE_INVOICES, SALES_INVOICES, supplier_by_code

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "data" / "generated"
OUT = ROOT / "data" / "out"


def eur(x) -> str:
    if x is None:
        return ""
    s = f"{Decimal(x):,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def finding_dict(f: Finding) -> dict:
    d = asdict(f)
    extra = d.pop("extra", None) or {}
    if extra.get("create_supplier"):
        d["create_supplier"] = extra["create_supplier"]  # UI rodo mygtuką „Sukurti tiekėją“
    return d


def counts(findings: list[Finding]) -> dict:
    return {
        "error": sum(f.severity == "error" for f in findings),
        "warning": sum(f.severity == "warning" for f in findings),
    }


def _download(name: str, label: str, meta: str = "") -> dict:
    return {"label": label, "url": f"/api/download/{name}", "meta": meta}


def ensure_demo_data() -> None:
    """Sugeneruoja fiktyvius demo failus, jei jų dar nėra."""
    from data import gen_bank, gen_invoices, gen_timesheet

    if not (GEN / "saskaitos").exists() or not any((GEN / "saskaitos").glob("*.pdf")):
        gen_invoices.main()
    if not (GEN / "bankas" / "israsas_2026-10.xml").exists():
        gen_bank.main()
    if not (GEN / "tabelis" / "tabelis_2026-10.xlsx").exists():
        gen_timesheet.main()


# ---------------- Sąskaitos ----------------
def run_invoices(folder: Path | None = None) -> dict:
    ensure_demo_data()
    return _invoices_result(folder or GEN / "saskaitos")


def mail_folder() -> Path:
    from core import mail_import

    custom_dir = mail_import._env("UNO_MAIL_DIR")
    return Path(custom_dir) if custom_dir else ROOT / "data" / "uploads" / "mail"


def mail_status_path() -> Path:
    """Pašto importo būsena (rašo tools/mail_poll.py, skaito app): šalia pašto aplanko."""
    folder = mail_folder()
    return folder.parent / (folder.name + "_status.json")


_mail_thread_lock = threading.Lock()


@contextmanager
def mail_import_lock(timeout: float = 600.0):
    """Tarp procesų (worker + rankinis mygtukas) užraktas pašto importui: parsisiuntimas + apdorojimas + publikavimas.
    Failo užraktas (fcntl Linux, msvcrt Windows) dingsta kartu su procesu; papildomai gijų užraktas tame pačiame procese."""
    folder = mail_folder()
    folder.parent.mkdir(parents=True, exist_ok=True)
    path = folder.parent / (folder.name + ".lock")
    deadline = time.monotonic() + timeout
    if not _mail_thread_lock.acquire(timeout=timeout):
        raise TimeoutError("Pašto importas jau vykdomas")
    handle = open(path, "a+b")
    try:
        while True:
            try:
                _flock(handle)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("Pašto importas jau vykdomas") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            _funlock(handle)
    finally:
        handle.close()
        _mail_thread_lock.release()


def _flock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _funlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def download_mail_pdfs(days: int = 30) -> list[dict]:
    """Parsisiunčia PDF iš Gmail į pašto aplanką (senus pakeičia tik sėkmingai prisijungus).
    Kviesti laikant mail_import_lock(): kitaip du importai gali persidengti."""
    from core import mail_import

    folder = mail_folder()
    # Pirma į unikalų laikiną aplanką: nepavykus prisijungti ankstesnės sąskaitos lieka vietoje.
    folder.parent.mkdir(parents=True, exist_ok=True)
    for stale in folder.parent.glob(folder.name + "_incoming*"):  # lieka tik iš nutrūkusių procesų (užraktas laikomas)
        shutil.rmtree(stale, ignore_errors=True)
    incoming = Path(tempfile.mkdtemp(prefix=folder.name + "_incoming_", dir=folder.parent))
    try:
        fetched = mail_import.fetch_invoice_pdfs(incoming, days=days)
        folder.mkdir(parents=True, exist_ok=True)
        new_names = {new.name for new in incoming.glob("*.pdf")}
        for new in incoming.glob("*.pdf"):
            new.replace(folder / new.name)  # kiekvienas failas keičiamas atomiškai
        for old in folder.glob("*.pdf"):
            if old.name not in new_names:
                old.unlink()
        _store_link_failures(incoming, folder)
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return fetched


def _link_failures_path(folder: Path) -> Path:
    from core import mail_import

    return folder.parent / (folder.name + mail_import.LINK_FAILURES_FILE)


def _store_link_failures(incoming: Path, folder: Path) -> None:
    """Šio importo nepavykusios nuorodos pakeičia ankstesnes (jei nepavykusių nėra, sena žinutė išnyksta)."""
    target = _link_failures_path(folder)
    from core import mail_import

    source = incoming / mail_import.LINK_FAILURES_FILE
    if source.exists():
        source.replace(target)
    else:
        target.unlink(missing_ok=True)


def _add_link_failure_findings(result: dict, folder: Path) -> None:
    import json

    try:
        failures = json.loads(_link_failures_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for item in failures if isinstance(failures, list) else []:
        info = Finding("invoices", "warning", "Sąskaitos nuorodos parsisiųsti nepavyko",
                       f"Siuntėjas: {item.get('sender_domain', '?')}; laiško nr. {item.get('uid', '?')}; "
                       f"nuorodos domenas: {item.get('host', '?')}. "
                       f"Priežastis: {item.get('reason', '')}.",
                       suggested_action="Atsisiųskite sąskaitą iš laiško ranka ir įkelkite.")
        result["findings"].append(finding_dict(info))
        result["_findings"].append(info)


def build_mail_result(fetched_count: int, days: int = 30) -> dict:
    """Pašto aplanko PDF -> tas pats konvejeris (AI laukai iš talpyklos, jei jau buvo)."""
    from core import mail_import

    folder = mail_folder()
    result = _invoices_result(folder, use_ai=mail_import._env("UNO_MAIL_AI") != "0",
                              ai_cache=folder.parent / (folder.name + "_ai_cache"))
    head = {"label": "Iš pašto", "value": f"{fetched_count} PDF"}
    result["kpis"].insert(0, head)
    result["_source"] = (folder, result["_source"][1], result["_source"][2], [head])
    result["_mail_polled"] = True
    _add_link_failure_findings(result, folder)
    if not fetched_count:
        info = Finding("invoices", "info", "Pašte naujų sąskaitų nerasta",
                       f"Per paskutines {days} d. laiškų su PDF priedais nerasta.")
        result["findings"].append(finding_dict(info))
        result["_findings"].append(info)
    return result


def run_invoices_mail(days: int = 30) -> dict:
    """Sąskaitos iš Gmail: PDF į UNO_MAIL_DIR (numatyta data/uploads/mail), tada tas pats konvejeris.
    Tikrai dėžutei UNO_MAIL_DIR rodo į UNO_DATA (už repo), o UNO_MAIL_AI=0 išjungia AI laukų ištraukimą."""
    with mail_import_lock():
        return build_mail_result(len(download_mail_pdfs(days)), days)


def read_mail_status() -> dict | None:
    import json

    try:
        data = json.loads(mail_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sync_mail_invoices(results: dict) -> None:
    """Serveris su atskiru pašto procesu (docker worker): po sėkmingo importo perkrauna rezultatą iš aplanko
    (be IMAP). Nekeičia ranka įkeltų / demo sąskaitų: perrašoma tik kai rezultato nėra arba jis jau buvo iš pašto."""
    status = read_mail_status()
    if not status or not status.get("ok") or not status.get("finished_at"):
        return
    current = results.get("invoices")
    if current is not None and not current.get("_mail_polled"):
        return
    if current is not None and current.get("_mail_finished_at") == status["finished_at"]:
        return
    with mail_import_lock():
        result = build_mail_result(int(status.get("pdf_count") or len(list(mail_folder().glob("*.pdf")))))
    result["_mail_finished_at"] = status["finished_at"]
    results["invoices"] = result


def rerun_invoices(result: dict) -> dict:
    """Perskaičiuoja tą patį aplanką (pvz. sukūrus tiekėją) be pašto parsiuntimo; AI rezultatai iš talpyklos."""
    folder, use_ai, ai_cache, head_kpis = result["_source"]
    new = _invoices_result(folder, use_ai=use_ai, ai_cache=ai_cache)
    new["kpis"][:0] = head_kpis
    if "_mail_polled" in result:
        _add_link_failure_findings(new, folder)
    for key in ("_mail_polled", "_mail_finished_at"):  # kitaip sync_mail_invoices ignoruotų vėlesnius worker'io importus
        if key in result:
            new[key] = result[key]
    return new


def _invoices_result(folder: Path, *, use_ai: bool = False, ai_cache: Path | None = None) -> dict:
    parsed, findings = invoices.process_folder(folder, use_ai=use_ai, ai_cache=ai_cache,
                                               only_company=folder == mail_folder())   # paštas: tik UNO sąskaitos
    # Sąskaitos su klaidomis (PVM, dublikatai, trūkstami laukai) sulaikomos, kol žmogus išspręs.
    held = {f.ref for f in findings if f.severity == "error"}
    export = [p for p in parsed if p.number not in held and p.lines and not p.missing_fields]
    OUT.mkdir(parents=True, exist_ok=True)
    rivile_export.purchases_to_xlsx(export, OUT / "rivile_pirkimai_2026-10.xlsx")
    rivile_gama_eip.purchases_to_eip(export, OUT / "rivile_gama_pirkimai_2026-10.eip")
    rivile_export.sales_to_xlsx(SALES_INVOICES, OUT / "rivile_pardavimai_2026-10.xlsx")
    sales_dir = OUT / "pardavimo_saskaitos"
    sales_dir.mkdir(exist_ok=True)
    for inv in SALES_INVOICES:
        invoices.render_sales_invoice_pdf(inv, sales_dir / f"{inv.number}.pdf")

    flagged = {f.ref: f.severity for f in findings}
    ok = export
    rows = [{
        "file": Path(p.source_file).name, "number": p.number, "supplier": p.supplier_name,
        "code": p.supplier_code or "?", "date": p.issue_date.isoformat() if p.issue_date else "",
        "net": eur(p.net), "vat": eur(p.vat), "gross": eur(p.gross),
        "conf": f"{p.confidence:.0%}", "_sev": flagged.get(p.number, ""),
    } for p in parsed]
    return {
        "kpis": [
            {"label": "Nuskaityta sąskaitų", "value": str(len(parsed))},
            {"label": "Paruošta importui", "value": f"{len(export)} (sulaikyta {len(parsed) - len(export)})"},
            # Sumos iš eilučių, kaip ir eksporte, kad deklaracija sutaptų su importu iki cento.
            {"label": "Suma be PVM", "value": eur(sum((l.net for p in ok for l in p.lines), Decimal(0)))},
            {"label": "PVM (paruoštų importui)", "value": eur(sum((l.vat for p in ok for l in p.lines), Decimal(0)))},
            {"label": "Su klaidomis", "value": str(counts(findings)["error"])},
        ],
        "findings": [finding_dict(f) for f in findings],
        "table": {"title": "Nuskaitytos pirkimo sąskaitos", "columns": [
            {"key": "number", "label": "Nr."}, {"key": "supplier", "label": "Tiekėjas"},
            {"key": "code", "label": "Rivilė kodas"}, {"key": "date", "label": "Data"},
            {"key": "net", "label": "Be PVM", "num": True}, {"key": "vat", "label": "PVM", "num": True},
            {"key": "gross", "label": "Su PVM", "num": True}, {"key": "conf", "label": "Tikslumas", "num": True},
        ], "rows": rows},
        "downloads": [
            _download("rivile_gama_pirkimai_2026-10.eip", "Rivilė GAMA importo failas (EIP)", "EIP / XML"),
            _download("rivile_pirkimai_2026-10.xlsx", "Rivilė ERP importas: pirkimai", "XLSX"),
            _download("rivile_pardavimai_2026-10.xlsx", "Rivilė ERP importas: pardavimai", "XLSX"),
            _download(f"pardavimo_saskaitos/{SALES_INVOICES[0].number}.pdf", "Pardavimo sąskaitos pavyzdys", "PDF"),
        ],
        "counts": counts(findings),
        "summary": f"{len(parsed)} sąskaitų nuskaityta, {counts(findings)['error']} su klaida, importo failas paruoštas",
        "_source": (folder, use_ai, ai_cache, []),
        "_findings": findings,
        "_export": export,      # paruoštos Rivilei (mašininis EIP eksportas, app/export_api.py)
    }


# ---------------- Bankas ----------------
def _remaining(p) -> Invoice:
    """Dalinai apmokėta sąskaita -> mokėjimas tik likučiui (viena eilutė be PVM = likutis)."""
    return replace(p.invoice, lines=(InvoiceLine("Likutis", Decimal(1), p.remaining, Decimal(0)),))


def run_bank(xml_path: Path | None = None) -> dict:
    ensure_demo_data()
    xml_path = xml_path or GEN / "bankas" / "israsas_2026-10.xml"
    txs = bank.parse_camt053(xml_path)
    r = bank.reconcile(txs, PURCHASE_INVOICES, SALES_INVOICES)
    OUT.mkdir(parents=True, exist_ok=True)
    to_pay = [u.invoice for u in r.unpaid_invoices] + [_remaining(p) for p in r.partial if p.invoice.direction == "purchase"]
    bank.build_pain001(to_pay, OUT / "mokejimai_pain001.xml", PERIOD_END)

    matched_ids = {tx.ref: (inv.number, bank.RULE_LABELS[rule]) for tx, inv, rule in r.matched}
    dup_ids = {d.transaction.ref for d in r.duplicates}
    unmatched_ids = {t.ref for t in r.unmatched_tx}
    other = {t.ref: cat for t, cat in r.other}
    partial_ids = {tx.ref for pp in r.partial for tx in pp.transactions}
    rows = []
    for t in txs:
        inv, how = matched_ids.get(t.ref, ("", ""))
        sev = "error" if t.ref in dup_ids else "warning" if t.ref in unmatched_ids else ""
        status = how or other.get(t.ref) or ("Dvigubas mokėjimas" if t.ref in dup_ids else "Trūksta sąskaitos" if sev else "Dalinis" if t.ref in partial_ids else "Nesulyginta")
        rows.append({"date": t.booking_date.isoformat(), "party": t.counterparty_name, "text": t.remittance,
                     "amount": eur(t.amount), "invoice": inv, "status": status, "_sev": sev})
    n_unpaid = len(r.unpaid_invoices)
    return {
        "kpis": [
            {"label": "Operacijų", "value": str(len(txs))},
            {"label": "Sulyginta automatiškai", "value": f"{len(r.matched)} iš {len(txs) - len(r.other)}"},
            {"label": "Neapmokėtos sąskaitos", "value": str(n_unpaid)},
            {"label": "Mokėtina", "value": eur(sum((i.gross for i in to_pay), Decimal(0)))},
            {"label": "Trūksta sąskaitų", "value": str(len(r.unmatched_tx))},
        ],
        "findings": [finding_dict(f) for f in r.findings],
        "table": {"title": "Banko operacijos ir sulyginimas", "columns": [
            {"key": "date", "label": "Data"}, {"key": "party", "label": "Gavėjas / mokėtojas"},
            {"key": "text", "label": "Paskirtis", "wrap": True}, {"key": "amount", "label": "Suma", "num": True},
            {"key": "invoice", "label": "Sąskaita"}, {"key": "status", "label": "Kaip sulyginta"},
        ], "rows": rows},
        "downloads": [_download("mokejimai_pain001.xml", f"Mokėjimų failas bankui ({n_unpaid} sąsk.)", "pain.001")],
        "counts": counts(r.findings),
        "summary": f"{len(r.matched)} mokėjimų sulyginta, {len(r.unmatched_tx)} be sąskaitos, {n_unpaid} neapmokėtos",
        "_findings": r.findings,
        "_recon": r,
    }


# ---------------- DU + tabelis ----------------
def run_payroll(timesheet_path: Path | None = None) -> dict:
    ensure_demo_data()
    tdir = GEN / "tabelis"
    ts_path = timesheet_path or tdir / "tabelis_2026-10.xlsx"
    ts_rows, ts_findings = timesheet.check(ts_path, tdir / "rivile_valandos_2026-10.xlsx",
                                           tdir / "sodra_nedarbingumai_2026-10.csv",
                                           tdir / "atostogu_prasymai_2026-10.csv",
                                           ym=(PERIOD_YEAR, PERIOD_MONTH),
                                           norms={e.tab_no: e.hours_norm for e in EMPLOYEES})
    pay_rows, summary = payroll.run_month(EMPLOYEES, ts_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    payroll.export_payroll_xlsx(pay_rows, summary, OUT / "du_2026-10_rivile.xlsx")

    flagged = {}
    for f in ts_findings:
        flagged[f.ref] = f.severity if f.severity == "error" or f.ref not in flagged else flagged[f.ref]
    rows = [{
        "tab": r.tab_no, "name": r.name, "net_target": eur(r.net_agreed), "gross": eur(r.gross),
        "gpm": eur(r.gpm), "sodra": eur(r.sodra_employee), "net": eur(r.net),
        "cost": eur(r.employer_cost), "_sev": flagged.get(r.tab_no, "") if flagged.get(r.tab_no) != "info" else "",
    } for r in pay_rows]
    return {
        "kpis": [
            {"label": "Darbuotojų", "value": str(len(pay_rows))},
            {"label": "Bruto viso", "value": eur(summary.gross)},
            {"label": "GPM (GPM313)", "value": eur(summary.gpm)},
            {"label": "Sodra viso (SAM)", "value": eur(summary.sodra_employee + summary.sodra_employer)},
            {"label": "Išmokėti neto", "value": eur(summary.net)},
        ],
        "findings": [finding_dict(f) for f in ts_findings],
        "table": {"title": "Atlyginimai: neto → bruto", "columns": [
            {"key": "tab", "label": "Tab."}, {"key": "name", "label": "Darbuotojas"},
            {"key": "net_target", "label": "Sutarta neto", "num": True}, {"key": "gross", "label": "Bruto", "num": True},
            {"key": "gpm", "label": "GPM", "num": True}, {"key": "sodra", "label": "Sodra", "num": True},
            {"key": "net", "label": "Neto", "num": True}, {"key": "cost", "label": "Darbo vietos kaina", "num": True},
        ], "rows": rows},
        "downloads": [_download("du_2026-10_rivile.xlsx", "DU žiniaraštis importui į Rivilę", "XLSX")],
        "counts": counts(ts_findings),
        "summary": f"{len(pay_rows)} darbuotojų bruto paskaičiuotas, {counts(ts_findings)['error'] + counts(ts_findings)['warning']} tabelio neatitikimai",
        "_findings": ts_findings,
        "_summary": summary,
        "_names": [r.name for r in ts_rows if r.name],
    }


def calc(net: Decimal, fixed_term: bool) -> dict:
    r = payroll.net_to_gross(net, fixed_term=fixed_term)
    lines = [
        ("Bruto (priskaičiuota)", r.gross), ("NPD", r.npd), ("GPM 20 %", -r.gpm),
        ("Sodra darbuotojo 19,5 %", -r.sodra_employee), ("Į rankas", r.net),
        ("Sodra darbdavio", r.sodra_employer), ("Darbo vietos kaina", r.employer_cost),
    ]
    return {"lines": [{"label": k, "value": eur(v), "total": k in ("Į rankas", "Darbo vietos kaina")} for k, v in lines]}


def supplier_email(code: str) -> str:
    return supplier_by_code(code).email
