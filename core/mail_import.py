"""Sąskaitų PDF atsisiuntimas iš Gmail (IMAP, tik skaitymas).

Prisijungimas per aplinkos kintamuosius UNO_IMAP_USER / UNO_IMAP_PASSWORD (Gmail programos slaptažodis).
Laiškai nežymimi perskaitytais ir netrinami (INBOX atidaromas readonly).
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from core import link_invoices, link_safety

log = logging.getLogger("uno.mail_import")
LINK_FAILURES_FILE = "_link_failures.json"
TIMEOUT_S = 30
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_NAME_LEN = 80
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailError(Exception):
    """Pašto prisijungimo ar skaitymo klaida (žinutė lietuviška, be slaptažodžio)."""


class MailNotConfigured(MailError):
    """Nenustatyti UNO_IMAP_USER / UNO_IMAP_PASSWORD."""


def _env(name: str) -> str:
    """Proceso aplinka, o Windows'e dar ir naudotojo kintamieji (setup_mail.ps1), kad nereiktų perkrauti serverio."""
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def _credentials() -> tuple[str, str]:
    user = _env("UNO_IMAP_USER")
    password = _env("UNO_IMAP_PASSWORD")
    if not user or not password:
        raise MailNotConfigured(
            "Paštas nesujungtas: nenustatyti UNO_IMAP_USER ir UNO_IMAP_PASSWORD. "
            "Paleiskite tools\\setup_mail.ps1 ir perkraukite programą.")
    return user, password


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def safe_filename(name: str, uid: str, index: int = 0) -> str:
    """Be kelio skirtukų ir keistų simbolių, unikalus (laiško uid + priedo nr.), ribotas ilgis, visada .pdf."""
    base = _decode(name).replace("\\", "/").split("/")[-1]
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    stem = re.sub(r"[^\w.\- ]", "_", stem, flags=re.UNICODE).strip(" .") or "saskaita"
    return f"{uid}_{index}_{stem[:MAX_NAME_LEN]}.pdf"


def _is_pdf(part: Message) -> bool:
    name = _decode(part.get_filename()).lower()
    return name.endswith(".pdf") or part.get_content_type() == "application/pdf"


def _mail_date(msg: Message) -> str:
    try:
        return parsedate_to_datetime(msg.get("Date")).date().isoformat()
    except Exception:
        return ""


def _save_pdfs(msg: Message, uid: str, dest: Path) -> list[dict]:
    saved = []
    for index, part in enumerate(msg.walk()):
        if part.is_multipart() or not _is_pdf(part):
            continue
        data = part.get_payload(decode=True) or b""
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            continue
        name = safe_filename(part.get_filename() or "saskaita.pdf", uid, index)
        (dest / name).write_bytes(data)
        saved.append({"file": name, "sender": _decode(msg.get("From")),
                      "subject": _decode(msg.get("Subject")), "date": _mail_date(msg)})
    return saved


def _imap_since(days: int) -> str:
    d = date.today() - timedelta(days=days)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"  # nepriklauso nuo lokalės


def _search_criteria(days: int) -> tuple[str, ...]:
    """Gmail: tik laiškai su PDF priedais (užimtoje dėžutėje `limit` nenueina į laiškus be sąskaitų)."""
    if "gmail" in os.environ.get("UNO_IMAP_HOST", "imap.gmail.com"):
        extra = _env("UNO_IMAP_QUERY").replace('"', "")  # pvz. tik sąskaitų žodžiai mišrioje dėžutėje
        query = f"has:attachment filename:pdf newer_than:{int(days)}d {extra}".strip()
        return ("X-GM-RAW", f'"{query}"')
    return ("SINCE", _imap_since(days))


def _link_search_criteria(days: int) -> tuple[str, ...] | None:
    """Gmail: antra paieška laiškams be PDF (sąskaita tik nuoroda). Kituose serveriuose pirma paieška jau apima visus."""
    if "gmail" not in os.environ.get("UNO_IMAP_HOST", "imap.gmail.com"):
        return None
    return ("X-GM-RAW", f'"newer_than:{int(days)}d -filename:pdf"')


def _link_domains() -> list[str]:
    raw = _env("UNO_INVOICE_LINK_DOMAINS")
    return link_invoices.parse_domains(raw if raw else None)


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.\-]+)", sender)
    return match.group(1).lower() if match else "nežinomas siuntėjas"


def _save_link_pdfs(msg: Message, uid: str, dest: Path, domains: list[str], failures: list[dict]) -> list[dict]:
    """PDF iš leistinų nuorodų (laiškui be PDF priedų). Nepavykusios -> failures (be žetonų, tema redaguota)."""
    links = link_invoices.extract_links(msg)
    allowed: list[str] = []
    for raw in links:
        canonical = link_safety.canonicalize(raw, domains)
        if canonical and canonical not in allowed:
            allowed.append(canonical)
    allowed = allowed[: link_invoices.MAX_LINKS_PER_MAIL]
    if len(links) > len(allowed):
        log.info("Laiškas %s: nesekta nuorodų (neleistini domenai / riba): %d", uid, len(links) - len(allowed))
    sender, subject = _decode(msg.get("From")), _decode(msg.get("Subject"))
    budget = link_invoices.LinkBudget()
    saved = []
    for n, url in enumerate(allowed):
        data, reason = link_invoices.fetch_link_pdf(url, domains, budget)
        if not data:
            log.info("Nuorodos PDF nepavyko %s: %s", link_safety.link_label(url), reason)
            failures.append({"sender_domain": _sender_domain(sender), "uid": uid,
                             "host": urlsplit(url).hostname or "?", "reason": reason})
            continue
        name = f"{uid}_L{n}.pdf"
        (dest / name).write_bytes(data)
        saved.append({"file": name, "sender": sender, "subject": subject, "date": _mail_date(msg), "source": "link"})
    return saved


def _process_message(raw: bytes, uid: str, dest: Path, domains: list[str], failures: list[dict]) -> list[dict]:
    """Vieno laiško klaida netrukdo kitiems: virsta įspėjimu (be laiško turinio)."""
    try:
        msg = email.message_from_bytes(raw)
        pdfs = _save_pdfs(msg, uid, dest)
        if not pdfs and domains:
            pdfs = _save_link_pdfs(msg, uid, dest, domains, failures)
        return pdfs
    except Exception as exc:
        log.warning("Laiškas %s: apdoroti nepavyko (%s)", uid, type(exc).__name__)
        failures.append({"sender_domain": "nežinomas siuntėjas", "uid": uid, "host": "?",
                         "reason": f"laiško apdoroti nepavyko ({type(exc).__name__})"})
        return []


def _search_uids(conn: imaplib.IMAP4_SSL, criteria: tuple[str, ...], limit: int) -> list[bytes]:
    status, data = conn.search(None, *criteria)
    if status != "OK":
        raise MailError("Nepavyko surasti laiškų pašto dėžutėje.")
    return list(reversed((data[0] or b"").split()[-limit:]))


def _write_link_failures(dest: Path, failures: list[dict]) -> None:
    """Nepavykusios nuorodos šalia PDF (app rodo kaip pastabas); jei nėra nepavykusių, failo nėra."""
    if failures:
        (dest / LINK_FAILURES_FILE).write_text(json.dumps(failures, ensure_ascii=False), encoding="utf-8")


def _connect(user: str, password: str) -> imaplib.IMAP4_SSL:
    host = os.environ.get("UNO_IMAP_HOST", "imap.gmail.com")
    port = int(os.environ.get("UNO_IMAP_PORT", "993"))
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT_S)
        conn.login(user, password)
        return conn
    except imaplib.IMAP4.error:
        raise MailError("Nepavyko prisijungti prie pašto: patikrinkite el. paštą ir Gmail programos "
                        "slaptažodį (reikia 2 žingsnių patvirtinimo).") from None
    except (OSError, ValueError):
        raise MailError("Nepavyko pasiekti pašto serverio: patikrinkite interneto ryšį.") from None


def fetch_invoice_pdfs(dest: Path, *, days: int = 30, limit: int = 50) -> list[dict]:
    """Išsaugo PDF priedus iš naujausių `limit` laiškų per paskutines `days` dienų."""
    user, password = _credentials()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    conn = _connect(user, password)
    results: list[dict] = []
    try:
        conn.select("INBOX", readonly=True)
        uids = _search_uids(conn, _search_criteria(days), limit)
        link_criteria = _link_search_criteria(days)
        if link_criteria:
            uids += [u for u in _search_uids(conn, link_criteria, limit) if u not in uids]
        domains, failures = _link_domains(), []
        for uid in uids:
            status, parts = conn.fetch(uid, "(BODY.PEEK[])")
            if status != "OK":
                raise MailError("Nepavyko atsisiųsti laiško iš pašto serverio; bandykite dar kartą.")
            if parts and isinstance(parts[0], tuple):
                results += _process_message(parts[0][1], uid.decode(), dest, domains, failures)
        _write_link_failures(dest, failures)
    except imaplib.IMAP4.error:
        raise MailError("Pašto serveris atmetė užklausą; bandykite dar kartą.") from None
    except OSError:
        raise MailError("Nutrūko ryšys su pašto serveriu; bandykite dar kartą.") from None
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return results
