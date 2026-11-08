"""AI tiltas: neatitikimai -> pseudonimizuotas tekstas Claude -> atkūrimas lokaliai.

Į Claude niekada nekeliauja vardai, asmens kodai, IBAN ar el. paštai: tik žymės.
Žemėlapis (žymė -> tikra reikšmė) lieka šiame kompiuteryje (vault/).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from core.anonymizer import Pseudonymizer, _person_pattern, find_leaks, person_names
from core.models import Finding
from core.world import EMPLOYEES

VAULT = Path(__file__).resolve().parent.parent / "vault"

PROMPT_HEADER = (
    "Esi buhalterės asistentas. Žemiau – mėnesio uždarymo neatitikimai. "
    "Žymės [DARB_xxx], [EMAIL_xxx] ir pan. yra pseudonimai, palik juos nepakeistus.\n"
    "Užduotis: kiekvienam atsakingam asmeniui parašyk trumpą, mandagų laišką lietuviškai "
    "su konkrečiais klausimais. Nieko neišgalvok, remkis tik pateiktais faktais.\n\n"
)


def _known_people() -> list[str]:
    return [e.full_name for e in EMPLOYEES]


def findings_to_text(findings: list[Finding]) -> str:
    lines = []
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. [{f.module}/{f.severity}] {f.title}\n   {f.detail}")
        if f.suggested_action:
            lines.append(f"   Siūlomas veiksmas: {f.suggested_action}")
    return "\n".join(lines)


# Atsarginis saugiklis tik banko paskirties vietoms: („...“) ir "paskirtis: „...“".
# Tiekėjų pavadinimai (UAB „Baltijos detalės“) lieka, nes nėra asmens duomenys.
QUOTED = re.compile(r"(?:(?<=\()|(?<=paskirtis: ))„[^“]*“")


def build_package(findings: list[Finding], extra_people: list[str] | None = None,
                  free_texts: list[str] | None = None, structured_names: list[str] | None = None) -> dict:
    """Paruošia tekstą Claude. Grąžina sesijos ID, anonimizuotą tekstą ir nutekėjimų patikrą.

    extra_people: vardai iš įkeltų failų (pvz. tabelio), kurių nėra demo darbuotojų sąraše.
    free_texts: laisvo teksto reikšmės (banko paskirtys), kurios pašalinamos visos, nepriklausomai nuo kabučių.
    structured_names: struktūrinių laukų vardai (banko kontrahentai, tiekėjų pavadinimai); asmenys (ne UAB/MB ...)
    pseudonimizuojami visada, o jei vardas vis tiek lieka tekste, paketas NEsaugus siųsti.
    """
    structured = person_names(structured_names or [])
    p = Pseudonymizer(known_people=_known_people() + list(extra_people or []) + structured)
    # Laisvas tekstas (banko paskirtis) į AI nekeliauja visai: jame gali būti bet kas.
    raw = findings_to_text(findings)
    for ft in sorted({x for x in (free_texts or []) if x}, key=len, reverse=True):
        raw = raw.replace(f"„{ft}“", "„[paskirtis pašalinta]“")  # tik visas kabutėse esantis fragmentas (K1)
    raw = QUOTED.sub("„[paskirtis pašalinta]“", raw)
    # Darbuotojų tabelio nr. irgi identifikuoja žmogų: keičiam į tą pačią DARB žymę.
    for e in EMPLOYEES:
        if e.tab_no in raw:
            raw = raw.replace(e.tab_no, p.token_for("DARB", e.full_name))
    # Struktūrinės reikšmės keičiamos tiksliu atitikmeniu (be , kad veiktų ir „VARDAS (IV)“), Sol S5 r2.
    for n in sorted(structured, key=len, reverse=True):
        raw = re.sub(re.escape(n), lambda m, n=n: p.token_for("DARB", n), raw, flags=re.IGNORECASE)
    text = PROMPT_HEADER + p.anonymize(raw)
    leaks = find_leaks(text)
    leaks += [f"Struktūrinis vardas liko tekste: {n}" for n in structured if _name_left(n, text)]
    session = _save(p)
    return {"session": session, "text": text, "leaks": leaks, "stats": p.stats(),
            "safe_to_send": not leaks}


def _name_left(name: str, text: str) -> bool:
    """Nepriklausoma patikra: ar tekste liko vardo branduolys (be skliaustų priedo, pvz. „(IV)“),
    nepaisant didžiųjų raidžių ir tarpų skaičiaus. Atskiri bendriniai žodžiai neskaičiuojami."""
    core = re.sub(r"\([^)]*\)", " ", name).split()
    if not core:
        return False
    pattern = r"(?<![a-ząčęėįšųūž])" + r"\s+".join(re.escape(w) for w in core) + r"(?![a-ząčęėįšųūž])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def restore(session: str, text: str) -> str:
    return _load(session).restore(text)


def _save(p: Pseudonymizer) -> str:
    VAULT.mkdir(exist_ok=True)
    session = uuid.uuid4().hex[:10]
    (VAULT / f"{session}.json").write_text(json.dumps(p.mapping, ensure_ascii=False), encoding="utf-8")
    return session


def _load(session: str) -> Pseudonymizer:
    path = VAULT / f"{session}.json"
    if not session.isalnum() or not path.exists():
        raise KeyError("Sesija nerasta")
    return Pseudonymizer(mapping=json.loads(path.read_text(encoding="utf-8")))

