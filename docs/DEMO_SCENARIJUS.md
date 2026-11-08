# UNO Parts apskaitos automatizavimas (demo, 2026-11-06)

Lokali sistema, kuri perima didžiąją dalį rutininio buhalterės darbo: sąskaitas, banką, darbo užmokestį, tabelį ir mėnesio kontrolę. Veikia be Rivilės API: įvestis yra failai (PDF, camt.053, Excel), išvestis yra Rivilė ERP importo failai.

Visi demo duomenys išgalvoti (`core/world.py`): 57 darbuotojai, 60 pirkimo sąskaitų, spalio banko išrašas.

## Paleidimas

1. Vieną kartą: `py -3.12 -m pip install fastapi "uvicorn[standard]" python-multipart openpyxl pdfplumber reportlab lxml`
2. Dukart spausti `paleisti.bat`, atsidaro http://localhost:8770
3. Interneto nereikia, šriftai ir ikonos lokaliai.

Testai: `py -3.12 -m pytest -q` (vieną kartą: `py -3.12 -m pip install pytest-playwright` ir `py -3.12 -m playwright install chromium`)

## Demo scenarijus (apie 12 min.)

| # | Ekranas | Ką darom | Ką pasakom |
|---|---|---|---|
| 1 | Apžvalga | Rodom kainoraštį: 7 eilutės, 2 000 €/mėn. | „Tai jūsų buhalterės sąskaita. Žiūrim, kurią dalį daro kodas.“ |
| 2 | Apžvalga | Spaudžiam **Paleisti uždarymą** | Per kelias sekundes visos eilutės gauna rezultatą. Svertinis vidurkis apie 65 % darbo. |
| 3 | Sąskaitos | Radiniai: PVM klaida vienoje sąskaitoje (3,40 €). Atsisiunčiam Rivilė importo XLSX | 60 sąskaitų nuskaityta be rankų. Klaidą pagavo aritmetika, ne žmogus. |
| 4 | Bankas | Dvigubas mokėjimas, dalinis, 2 mokėjimai be sąskaitos, 3 neapmokėtos. Atsisiunčiam pain.001 | Mokėjimų failas paruoštas, bet tvirtina žmogus banke. Permokos grąžinimo laiškas jau parašytas. |
| 5 | DU ir tabelis | Skaičiuoklėje įvedam neto (pvz. 1 450 €), matom bruto, GPM, Sodrą | Jūs mokate neto, todėl kas mėnesį 57 kartus skaičiuojama atgal. Čia tai daro kodas iki cento. |
| 6 | DU ir tabelis | Radiniai: viršvalandžiai virš ribos, atostogos be prašymo, liga be pažymos, 0 val., tabelis nesutampa su Rivile | Kiekvienam paruoštas klausimas vadovui. |
| 7 | Kontrolė ir AI | Laiškų juodraščiai, deklaracijų sumos (GPM313, SAM, PVM) | Deklaracijas generuoja Rivilė, mes patikrinam sumas prieš pateikimą. |
| 8 | Kontrolė ir AI | **Paruošti tekstą Claude**: žalia patikra, vardai ir kodai virto žymėmis. Kopijuojam į Claude, atsakymą įklijuojam, **Atkurti** | Claude nemato nė vieno vardo, asmens kodo ar IBAN. Tikri duomenys grįžta tik šiame kompiuteryje. |

Savo failus galima įkelti mygtuku „Įkelti“ kiekviename modulyje (PDF sąskaitos, camt.053, tabelis .xlsx).

## Ką sakyti atvirai

- Rivilė ERP be API (patikrinta 2026-11-04, `research/rivile_erp_import.md`): tiekėjus galima importuoti XLSX (`Clients`), banko išrašą Rivilė ima pati (camt.053), o pirkimo sąskaitos XLSX importas įkelia tik eilutes į jau sukurtą dokumentą. Todėl dabar sistema paruošia tiekėjų importą, eilučių importą ir antraščių suvestinę, o pilnai automatinis sąskaitų sukūrimas Rivilėje yra 2 etapas per Rivilė API. Stulpelių pavadinimus prieš gyvą naudojimą sutikrinti su jų atsisiųstu šablonu (`core/rivile_export.py`, `COLUMNS`).
- DU demo supaprastinimai: atostoginiai ir ligos 1-2 d. skaičiuojami nuo šio mėnesio sutarto atlyginimo, ne nuo 3 mėn. VDU. Ligos išmoka apmokestinama tik 15 % GPM, be Sodros (VMI). Pensijų kaupimas (3 %) ir NPD neviso etato darbuotojams pilote. DU failas dabar paprasta lentelė, Rivilės DU importo šablonas derinamas pilote.
- Personalas: priėmimas, atleidimas ir 1-SD / 2-SD duomenys demo nedaromi (pilotas). Pardavimo sąskaitos sukuriamos PDF, siuntimas klientams pilote.
- Pirkimų eilutės visų sąskaitų viename lape su pagalbiniu `DocumentNumber`: Rivilė eilutes ima po vieną dokumentą, todėl pilote skaidysim po failą arba naudosim API.
- Automatizacijos procentai yra įvertis, pilote pamatuosim realiai. Metiniai GPM 25 % / 32 % slenksčiai netaikomi (57 darbuotojams neaktualu). Tarifai 2026: `core/rates_2026.py` su šaltiniais.
- Pseudonimizuoti duomenys pagal BDAR vis tiek yra asmens duomenys. Reikia Claude Team / API su DPA, ne asmeninės paskyros.
- Pateikimas VMI, Sodrai ir mokėjimų tvirtinimas banke lieka žmogui.

## Kitas žingsnis (pilotas)

1. 2-3 tikri jų failai: tabelis, banko išrašas, kelios sąskaitos, Rivilės DU eksportas.
2. Suderinti importo šablonus su jų Rivile.
3. Vienas mėnuo lygiagrečiai su buhaltere, lyginam rezultatus.

## Perduodant failus klientui

Išimti `.flow/` (vidiniai audito žurnalai) ir `vault/`.

## Personalo prašymai (atostogos ir komandiruotės, /hr)

Atskiras modulis: `core/hr/` (verslo logika), `app/hr_api.py` (API `/api/hr/*`), `app/static/hr*.html|css|js` (UI). Pasirašymas imituojamas Dokobit formos adapteriu (`HR_SIGNING=demo`). Specifikacija: `.flow/2026-11-07-personalas-atostogos/SPEC.md`.

| Aplinkos kintamasis | Paskirtis |
|---|---|
| `HR_DATABASE_URL` arba `DATABASE_URL` | Postgres (Neon). Nenustačius naudojamas SQLite `data/hr.db` (rašymo transakcijos `BEGIN IMMEDIATE`) |
| `HR_DEMO=1` | demo prisijungimas pagal asmenį ir `POST /api/hr/demo/reset` |
| `HR_POSTBACK_SECRET` | bendra paslaptis `POST /api/hr/postback` antraštei `X-Postback-Secret` (be jos postback atmetamas) |
| `HR_SECURE_COOKIE=1` | Secure sesijos slapukas; automatiškai įjungiamas, kai `VERCEL=1` |
| `HR_BASE_URL` | nuorodų laiškuose pagrindas (kitaip imamas iš užklausos) |
| `HR_SIGNING` | `demo` (numatytai) arba `dokobit` (dar nerealizuota) |
| `RESEND_API_KEY`, `HR_MAIL_ALLOWLIST` | laiškai siunčiami per Resend tik gavėjams iš sąrašo (kableliais); kitaip lieka demo pašto dėžutėje |

Prisijungimo nuorodų limitas (el. paštas ir IP, 15 min.) saugomas DB lentelėje `hr_login_attempts`.

## Diegimas į VPS (Docker)

`deploy/`: `docker-compose.yml` (app, worker, marsrutai, postgres, caddy, backup), `install.sh`, `update.sh`, `.env.example`, `backup-restore.md`, žingsniai lietuviškai `deploy/DIEGIMAS_VPS.md`. Pašto importas be naršyklės: `py -3.12 -m tools.mail_poll`. AI laukai: `UNO_AI_PROVIDER` = `cli` (numatyta lokaliai) | `vertex` (VPS) | `anthropic`.

## Struktūra

```
core/        skaičiavimai (models, rates_2026, world, invoices, rivile_export, bank, payroll, timesheet, control, anonymizer, ai_bridge)
app/         FastAPI serveris + UI (static/)
data/        demo duomenų generatoriai; generated/ ir out/ kuriami automatiškai
tests/       110 testų (iš jų 21 Playwright e2e: tests/e2e)
```
