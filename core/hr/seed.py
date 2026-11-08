"""Demo duomenys: išgalvota „Demo įmonė, UAB“ (12 darbuotojų). Be asmens kodų. Pasėjimas idempotentiškas."""
from __future__ import annotations

import logging

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.hr import db
from core.hr.documents import COMPANY
from core.hr.schema import (hr_audit, hr_documents, hr_employees, hr_magic_links, hr_marks, hr_meta, hr_outbox,
                            hr_requests, hr_signers)

log = logging.getLogger(__name__)
SEED_VERSION = "1"
DOMAIN = "demo.uno.lt"

# id, vardas, pareigos, skyrius, vadovo id, HR, likutis (d.)
EMPLOYEES = [
    (1, "Tomas Vaitkus", "Direktorius", "Administracija", 1, False, 24.0),
    (2, "Rasa Petrauskienė", "Sandėlio vadovė", "Sandėlis", 1, False, 18.0),
    (3, "Mantas Jankauskas", "Pardavimų vadovas", "Pardavimai", 1, False, 12.0),
    (4, "Giedrė Kazlauskienė", "Administracijos vadovė", "Administracija", 1, False, 21.0),
    (5, "Jonas Stankevičius", "Sandėlininkas", "Sandėlis", 2, False, 3.5),
    (6, "Lukas Žukauskas", "Krovinių operatorius", "Sandėlis", 2, False, 9.0),
    (7, "Ieva Butkutė", "Komplektuotoja", "Sandėlis", 2, False, 15.5),
    (8, "Dovilė Paulauskaitė", "Pardavimų vadybininkė", "Pardavimai", 3, False, 7.0),
    (9, "Andrius Kavaliauskas", "Pardavimų vadybininkas", "Pardavimai", 3, False, 24.0),
    (10, "Austėja Navickaitė", "Klientų aptarnavimo specialistė", "Pardavimai", 3, False, 5.5),
    (11, "Jūratė Baltrūnienė", "Buhalterė", "Administracija", 4, True, 14.0),
    (12, "Mindaugas Sakalauskas", "Pirkimų specialistas", "Administracija", 4, False, 10.0),
]
_ASCII = str.maketrans("ąčęėįšųūžĄČĘĖĮŠŲŪŽ", "aceeisuuzACEEISUUZ")
_ready: set[str] = set()


def email_for(name: str) -> str:
    return f"{name.split()[0].translate(_ASCII).lower()}@{DOMAIN}"


def _values(row: tuple) -> dict:
    emp_id, name, position, department, manager_id, is_hr, balance = row
    return {"id": emp_id, "company": COMPANY, "name": name, "email": email_for(name), "position": position,
            "department": department, "manager_id": manager_id, "is_hr": is_hr, "balance": balance, "active": True}


def _seed_employees(conn) -> None:
    existing = {r[0] for r in conn.execute(select(hr_employees.c.id))}
    for row in EMPLOYEES:
        values = _values(row)
        if values["id"] in existing:
            conn.execute(update(hr_employees).where(hr_employees.c.id == values["id"]).values(**values))
        else:
            conn.execute(insert(hr_employees).values(**values))
    conn.execute(delete(hr_meta).where(hr_meta.c.key == "seed_version"))
    conn.execute(insert(hr_meta).values(key="seed_version", value=SEED_VERSION))


def ensure_seeded(conn) -> bool:
    """Pasėja tik jei hr_meta seed_version nesutampa. True, jei pasėta."""
    current = conn.execute(select(hr_meta.c.value).where(hr_meta.c.key == "seed_version")).scalar()
    if current == SEED_VERSION:
        return False
    _seed_employees(conn)
    return True


def reset(conn) -> None:
    """Išvalo prašymus, dokumentus, žymėjimus, laiškus ir atstato darbuotojus. Sesijos išlieka."""
    for table in (hr_audit, hr_marks, hr_signers, hr_documents, hr_requests, hr_outbox, hr_magic_links):
        conn.execute(delete(table))
    _seed_employees(conn)


def ready():
    """Variklis su sukurtomis lentelėmis ir demo duomenimis (vieną kartą per procesą ir DB)."""
    engine = db.get_engine()
    key = str(engine.url)
    if key not in _ready:
        try:
            with db.tx() as conn:
                ensure_seeded(conn)
        except IntegrityError:  # kitas šaltas startas pasėjo vienu metu
            log.warning("HR demo duomenų pasėjimas: lygiagretus startas jau pasėjo (IntegrityError), praleidžiama")
        _ready.add(key)
    return engine
