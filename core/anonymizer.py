"""Lokalus pseudonimizatorius: asmens duomenys -> žymės ([DARB_001], [AK_001] ...) ir atgal.

Viskas vyksta šiame kompiuteryje. Į Claude keliauja tik tekstas su žymėmis,
o žemėlapis (žymė -> tikra reikšmė) lieka lokaliai.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Dažni LT vardai: padeda pagauti žmones, kurių nėra darbuotojų sąraše.
COMMON_FIRST_NAMES = {
    "Jonas", "Petras", "Antanas", "Tomas", "Mantas", "Lukas", "Darius", "Andrius", "Paulius",
    "Mindaugas", "Marius", "Justinas", "Rokas", "Vytautas", "Algirdas", "Giedrius", "Saulius",
    "Tadas", "Karolis", "Donatas", "Arnas", "Domantas", "Evaldas", "Gintaras", "Rimantas",
    "Ona", "Rūta", "Asta", "Jurgita", "Rasa", "Lina", "Inga", "Eglė", "Agnė", "Simona",
    "Greta", "Ieva", "Kristina", "Laura", "Vaida", "Aistė", "Indrė", "Gabija", "Milda", "Neringa",
}

# Eilutės su sveikatos informacija (BDAR 9 str.) išmetamos visai, ne pseudonimizuojamos.
HEALTH_LINE = re.compile(r"(?im)^(.*?\b(diagnozė|diagnoze|liga|susirgimas|ligos priežastis)\b\s*[:\-]).*$")

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s*[A-Z0-9]{4}){2,7}(?:\s*[A-Z0-9]{1,4})?\b", re.IGNORECASE)  # visų šalių IBAN
AK_RE = re.compile(r"\b[1-6]\d{10}\b")  # bet koks tikėtinas asmens kodas, net su klaidingu kontroliniu

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("PVM", re.compile(r"\bLT\d{9}(?:\d{3})?\b")),  # prieš IBAN, kad LT PVM kodas nebūtų laikomas IBAN
    ("IBAN", IBAN_RE),
    ("AK", AK_RE),
    ("TEL", re.compile(r"(?:\+370|\b8)[\s-]?(?:\d[\s-]?){7}\d\b")),
    ("IMK", re.compile(r"(?<![\d-])\b\d{7,9}\b(?!-)")),  # įmonių kodai 7-9 sk., ne sąskaitų nr. dalis (001-202610013)
    ("ADR", re.compile(r"\b[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?:io|ės|os|ų)?\s(?:g\.|gatvė|pr\.|al\.)\s?\d+[A-Za-z]?(?:-\d+)?\b")),
    ("TAB", re.compile(r"\bD\d{3}\b")),  # tabelio numeriai, net nežinomų darbuotojų
]

LABELS = {
    "DARB": "Darbuotojas / asmuo",
    "AK": "Asmens kodas",
    "IBAN": "Banko sąskaita",
    "PVM": "PVM kodas",
    "IMK": "Įmonės kodas",
    "TEL": "Telefonas",
    "EMAIL": "El. paštas",
    "ADR": "Adresas",
    "TAB": "Tabelio nr.",
}

TOKEN_RE = re.compile(r"\[(DARB|AK|IBAN|PVM|IMK|TEL|EMAIL|ADR|TAB)_(\d{3})\]")


def is_valid_personal_code(code: str) -> bool:
    """LT asmens kodo kontrolinio skaitmens patikra."""
    if not re.fullmatch(r"\d{11}", code):
        return False
    digits = [int(c) for c in code]
    for weights in ([1, 2, 3, 4, 5, 6, 7, 8, 9, 1], [3, 4, 5, 6, 7, 8, 9, 1, 2, 3]):
        remainder = sum(d * w for d, w in zip(digits, weights)) % 11
        if remainder != 10:
            return remainder == digits[10]
    return digits[10] == 0


def _stem(word: str) -> str:
    """Grubus LT kamienas, kad pagautume linksnius: Jonas/Jono/Jonui, Eglė/Eglės."""
    for ending in ("ienė", "aitė", "ytė", "utė", "ūtė", "ius", "as", "is", "ys", "us", "ė", "a"):
        if word.lower().endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _person_pattern(full_name: str) -> re.Pattern:
    parts = full_name.split()
    stems = [re.escape(_stem(p)) + r"[a-ząčęėįšųūž]{0,5}" for p in parts]
    return re.compile(r"\b" + r"\s+".join(stems) + r"\b", re.IGNORECASE)  # ir "KRISTINA NAVICKAITĖ"


@dataclass
class Pseudonymizer:
    known_people: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)  # žymė -> tikra reikšmė
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def token_for(self, kind: str, value: str) -> str:
        key = (kind, _normalize(kind, value))
        if key not in self._reverse:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            token = f"[{kind}_{self._counters[kind]:03d}]"
            self._reverse[key] = token
            self.mapping[token] = value
        return self._reverse[key]

    def anonymize(self, text: str) -> str:
        text = HEALTH_LINE.sub(lambda m: m.group(1) + " [PAŠALINTA: sveikatos duomenys]", text)

        # 1. Žinomi žmonės (darbuotojų sąrašas) – patikimiausias sluoksnis.
        for name in sorted(self.known_people, key=len, reverse=True):
            text = _person_pattern(name).sub(lambda m, n=name: self.token_for("DARB", n), text)

        # 2. Struktūriniai identifikatoriai.
        for kind, pattern in PATTERNS:
            text = pattern.sub(lambda m, k=kind: self._replace(k, m.group(0)), text)

        # 3. Nežinomi žmonės: dažnas vardas + pavardė.
        first = "|".join(sorted(COMMON_FIRST_NAMES, key=len, reverse=True))
        unknown = re.compile(rf"\b(?:{first})\s+[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]{{2,}}\b")
        return unknown.sub(lambda m: self.token_for("DARB", m.group(0)), text)

    def _replace(self, kind: str, value: str) -> str:
        return self.token_for(kind, value)

    def restore(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.mapping.get(m.group(0), m.group(0)), text)

    def stats(self) -> dict[str, int]:
        return {LABELS[k]: v for k, v in self._counters.items()}


def _normalize(kind: str, value: str) -> str:
    if kind in ("IBAN", "TEL"):
        return re.sub(r"[\s()]", "", value)
    return value.strip().lower() if kind == "EMAIL" else value.strip()


def find_leaks(text: str) -> list[str]:
    """Paskutinė patikra prieš siunčiant: ar neliko akivaizdžių identifikatorių."""
    leaks = [f"Asmens kodas: {x}" for x in AK_RE.findall(text)]
    leaks += [f"IBAN: {m.group(0)}" for m in IBAN_RE.finditer(text)]
    leaks += [f"El. paštas: {x}" for x in re.findall(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", text)]
    leaks += [f"Tabelio nr.: {x}" for x in re.findall(r"\bD\d{3}\b", text)]
    leaks += [f"Galimas asmens vardas: {x}" for x in possible_names(text)]
    return leaks


# ---------- S5: struktūriniai vardai ir konservatyvi vardų patikra ----------

COMPANY_WORDS = {"uab", "mb", "ab", "iį", "vši", "kb", "tūb", "žūb", "gmbh", "ltd", "sia", "oü", "as", "oy", "bv",
                 "sas", "llc", "inc", "sp", "bank", "bankas", "group", "grupė", "grupe", "ag", "sa", "srl", "spa"}


def is_company_name(name: str) -> bool:
    """Teisinė forma (UAB, MB, GmbH ...) = įmonė, ne fizinis asmuo; tokie pavadinimai nepseudonimizuojami."""
    words = re.findall(r"[\wąčęėįšųūž]+", name.casefold())
    return any(w in COMPANY_WORDS for w in words)


def person_names(names) -> list[str]:
    """Struktūrinių laukų vardai, kurie nėra įmonės: juos būtina pseudonimizuoti."""
    return sorted({n.strip() for n in names if n and n.strip() and not is_company_name(n)})


# Žodžiai, kurie gali būti didžiąja raide, bet nėra asmens vardai (įmonių / institucijų / šablonų žodžiai).
NAME_ALLOWLIST = {w.casefold() for w in (
    "Sąskaita", "Sąskaitos", "Sąskaitų", "Siūlomas", "Mokėjimas", "Mokėjimai", "Neapmokėta", "Įplauka", "Trūksta",
    "Dublikatas", "Atostogos", "Nežinomas", "Nepavyko", "Esi", "Žymės", "Užduotis", "Lietuvos", "Lietuva", "Bankas",
    "Swedbank", "Luminor", "Revolut", "Paysera", "Rivilė", "Sodra", "Sodros", "Valstybinė", "Mokesčių", "Inspekcija",
    "Pridėtinės", "Vertės", "Mokestis", "Darbo", "Užmokestis", "Banko", "Grupė", "Group", "Baltijos", "Vilnius",
    "Vilniaus", "Kaunas", "Kauno", "Klaipėda", "Klaipėdos", "Pirmadienis", "Antradienis", "Trečiadienis",
    "Ketvirtadienis", "Penktadienis", "Šeštadienis", "Sekmadienis", "Sausio", "Vasario", "Kovo", "Balandžio",
    "Gegužės", "Birželio", "Liepos", "Rugpjūčio", "Rugsėjo", "Spalio", "Lapkričio", "Gruodžio", "Tabelis", "Tabelio",
    "Darbuotojas", "Darbuotojai", "Įmonė", "Įmonės", "Paskirtis", "Prašymas", "Prašymai", "Kontrolė", "Deklaracija",
    "Paprašyti", "Patikrinti", "Nevesti", "Suma", "Sumos", "Data", "Bendra", "Bendras", "Likutis", "Gavėjas",
    "Mokėtojas", "Tiekėjas", "Pirkėjas", "Pardavėjas", "Gautas", "Gauta", "Sumokėta", "Neatitikimas", "Neatitikimai",
    "Klaida", "Įspėjimas", "Pastaba", "Trūkstami", "Laukai", "Eilutė", "Eilutės", "Eur", "Euras", "Mėnesio",
    "Mėnuo", "Spalis", "Lapkritis", "Rugsėjis", "Gruodis", "Sausis", "Vasaris", "Kovas", "Balandis", "Gegužė",
    "Birželis", "Liepa", "Rugpjūtis", "Mokėjimo", "Pavedimas", "Pavedimo", "Dalinis", "Dvigubas", "Nesulyginta",
)}
_CAP = "A-ZĄČĘĖĮŠŲŪŽ"
_LOW = "a-ząčęėįšųūž"
_NAME_PAIR = re.compile(rf"(?<![\w\[])([{_CAP}][{_LOW}]{{2,}})[^\S\n]+([{_CAP}][{_LOW}]{{2,}})\b")


def possible_names(text: str) -> list[str]:
    """Dviejų didžiosiomis raidėmis prasidedančių žodžių sekos, kurių nėra leidžiamų žodžių sąraše (konservatyviai)."""
    out = []
    for m in _NAME_PAIR.finditer(text):
        if m.group(1).casefold() in NAME_ALLOWLIST or m.group(2).casefold() in NAME_ALLOWLIST:
            continue
        out.append(m.group(0))
    return out
