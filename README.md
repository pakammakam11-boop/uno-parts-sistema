# UNO Parts sistema

Vidinė UAB Uno parts sistema: pirkimo sąskaitos iš pašto automatiškai keliauja į Rivilę GAMA, plius personalo (atostogų) e. parašai, savininko apžvalga ir apskaitos patikros. Python 3.12, FastAPI, SQLAlchemy, vanilla JS (be build žingsnio).

## Kas sukurta

| Modulis | Ką daro | Kur kodas |
|---|---|---|
| **Sąskaitos iš pašto → Rivilė** | Skaito `<SASKAITU_PASTAS>@gmail.com` (IMAP, tik skaitymas), PDF priedus ir Agnum nuorodas. AI (Vertex Gemini, ES) ištraukia laukus. Ima tik sąskaitas, kurių pirkėjas UAB Uno parts (305953678). Tiekėją randa Rivilės kartotekoje, tikrina sumas, PVM, dublikatus. Paruošia Rivilė GAMA EIP failą. | `core/mail_import.py`, `core/invoices.py`, `core/invoice_ai.py`, `core/link_invoices.py`, `core/rivile_gama_eip.py`, `app/services.py` |
| **Mašininis eksportas Rivilės serveriui** | `GET /api/export/gama-eip?format=zip` grąžina tik naujas sąskaitas (EIP + PDF), `POST …/ack?batch=` patvirtina. Nepatvirtinta siunta grąžinama vėl, patvirtinta niekada antrą kartą. Raktas `X-UNO-Token`. UI rodo, kada serveris kreipėsi paskutinį kartą (`/api/rivile-sync`). | `app/export_api.py` |
| **Rivilės serverio (VM) skriptai** | Suplanuota užduotis kas minutę parsiunčia naujas sąskaitas į `C:\UNOPARTS_IMPORT`. | `deploy/rivile-vm/` (žr. `SKAITYK.txt`) |
| **Personalas (HR)** | Atostogų prašymai: darbuotojas → pavaduojantis → vadovas pasirašo (Dokobit imitacija, Universal API forma). | `core/hr/`, `app/hr_api.py`, `app/static/hr*` |
| **Savininko apžvalga** | Pardavimai, kaštai, prognozė (demo duomenys, jungčių sluoksnis šaltiniams). | `core/dashboard/`, `app/dashboard_api.py` |
| **Apskaitos demo** | Bankas (camt.053), DU neto→bruto 2026, tabelis, mėnesio kontrolė, pseudonimizuotas AI paketas. | `core/bank.py`, `core/payroll*.py`, `core/timesheet.py`, `core/control.py`, `core/anonymizer.py` |
| **GAMA įrankiai** | Rivilės MS SQL tik skaitymui: tiekėjų kartoteka, pirkimų kodai (PowerShell per WinRM). | `tools/gama_*.ps1`, `core/gama*.py` |

Maršrutų modulis (`/marsrutai`) yra atskiras kolegos projektas, čia tik prijungiamas (`deploy/Dockerfile.marsrutai`, Vercel rewrite).

## Srautas

```
Tiekėjas → Liutauro paštas → (pašto taisyklė) → <SASKAITU_PASTAS>@gmail.com
  → UNO sistema: IMAP → PDF / Agnum nuoroda → AI laukai → tik UNO → tiekėjas iš Rivilės → patikros
  → /api/export/gama-eip (ZIP: EIP + PDF)
  → Rivilės VM: SISTEMA\uno_import.bat kas minutę → C:\UNOPARTS_IMPORT\IMPORTUI\*.eip, PDF\<mėn>\*.pdf
  → Rivilė GAMA: Pirkimai → Pirkimo dokumentai → Importas → Išorinis importas (arba SS_IMP_CIKL aplanko importas)
```

EIP formatas atkartoja Rivilės pačios `.EIP` failus: be XML antraštės ir šakninio elemento, viena žymė eilutėje, windows-1257.

## Paleidimas lokaliai

```
py -3.12 -m pip install -r requirements.txt
py -3.12 -m uvicorn app.main:app --port 8770
```

Atsidaro http://localhost:8770 (`/` apžvalga, `/apskaita`, `/hr`). Testai: `py -3.12 -m pytest -q` (Playwright testams: `py -3.12 -m playwright install chromium`).

## Diegimas

| Kur | Kaip |
|---|---|
| **Vercel** (dabartinis demo) | `deploy/vercel/` adapteris (`index.py`, `vercel.json` su `regions: fra1`, Neon Postgres fra1). Visa svetainė už HTTP Basic slaptažodžio (`UNO_SITE_PASSWORD`), `/api/export/*` su savo raktu. Agnum nuorodos per HTTP API, be naršyklės (`UNO_LINK_BROWSER=0`). |
| **VPS** (produkcijai) | `deploy/docker-compose.yml` + `deploy/install.sh`, instrukcija `deploy/DIEGIMAS_VPS.md`. Caddy, Postgres, worker (paštas), linkfetch (izoliuota naršyklė), backup. |
| **Rivilės VM** | `deploy/rivile-vm/`: nukopijuoti į `C:\UNOPARTS_IMPORT\SISTEMA\`, raktą į `SISTEMA\config\token.txt`, paleisti `uno_import.bat install`. |

Aplinkos kintamųjų sąrašas su paaiškinimais: `deploy/.env.example`. Svarbiausi: `UNO_IMAP_USER`, `UNO_IMAP_PASSWORD` (Gmail programos slaptažodis), `UNO_AI_PROVIDER=vertex`, `GOOGLE_SA_KEY_B64`, `GOOGLE_CLOUD_PROJECT`, `DATABASE_URL`, `UNO_EXPORT_TOKEN`, `UNO_SITE_PASSWORD` (Vercel). Jokių reikšmių repo nėra.

## Ką žinoti

- Į Rivilės DB niekas nerašoma: tik skaitymas (kartoteka) ir EIP failai, kuriuos importuoja pati Rivilė.
- EIP importas Rivilėje dar tvirtinamas su buhaltere (žr. `docs/UNO_saskaitu_automatizavimas_veikimo_planas.docx`). Tikslas vėliau: Rivilė API v2 (`EDIT_I06_FULL`), reikia Rivilės paslaugos ir Manorivile-Gateway.
- Saugumo auditas (Codex): S1–S7 ištaisyti. Paskutinių nuorodų ir PDF ribų pataisų pakartotinė peržiūra dar nedaryta.
- Demo duomenys išgalvoti (`core/world.py`); tikri UNO duomenys laikomi už repo (`UNO_DATA`).
- Senesnis workshop demo aprašas: `docs/DEMO_SCENARIJUS.md`.
