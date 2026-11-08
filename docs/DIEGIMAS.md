# UNO apskaita: diegimas, atsarginės kopijos, perkėlimas

Visos komandos vykdomos programos aplanke (ten, kur yra `requirements.txt`), per PowerShell arba CMD.

## 1. Diegimas švariame Windows kompiuteryje

1. Įdiekite **Python 3.12** (python.org, pažymėkite "Add python.exe to PATH" ir "py launcher"). Patikra: `py -3.12 --version`.
2. Nukopijuokite programos aplanką (pvz. į `C:\UNO`).
3. Įdiekite bibliotekas:
   ```
   cd C:\UNO
   py -3.12 -m pip install -r requirements.txt
   ```
   Gamybai pakanka pirmų 6 eilučių; `pytest` ir `pytest-playwright` reikalingi tik testams.
4. Sukurkite pirmą įmonę (jei duomenų bazė nauja):
   ```
   py -3.12 -m app.admin init --company "UAB Pavadinimas"
   ```
5. Sukurkite naudotojus (slaptažodžio klaus komanda, mažiausiai 12 simbolių):
   ```
   py -3.12 -m app.auth create-user --company 1 --username administratorius --role admin
   py -3.12 -m app.auth create-user --company 1 --username grazina --role accountant
   ```
   Rolės: `admin`, `accountant` (buhalteris), `manager` (vadovas).
6. Paleiskite: dukart spustelėkite `paleisti_app.bat`. Atsidarys http://127.0.0.1:8780/app. Programa klauso tik šio kompiuterio adreso (127.0.0.1). Norint jungtis iš kitų kompiuterių, reikia atskiro sprendimo (HTTPS atvirkštinis tarpininkas), šio paketo to nedaro.

Gamybinis paleidimas nustato `UNO_DEMO=0` ir `UNO_DB=data\uno.db`.

## 2. Kasdienė atsarginė kopija

Rankiniu būdu:
```
py -3.12 -m app.admin backup --out D:\UNO-kopijos
```
Sukuriamas `uno-backup-YYYYMMDD-HHMMSS.zip`: nuosekli duomenų bazės kopija (SQLite backup API, galima daryti ir veikiant programai), visi įkelti failai (`data\uploads`) ir `manifest.json` su kiekvieno failo SHA-256, programos versija ir taisyklių versija.

Patikra: `py -3.12 -m app.admin verify D:\UNO-kopijos\uno-backup-....zip`

Automatiškai kasdien 22:00 (Task Scheduler, vienas komandų eilutės langas, administratoriaus teisėmis nebūtina):
```
schtasks /Create /SC DAILY /ST 22:00 /TN "UNO atsarginė kopija" /TR "cmd /c cd /d C:\UNO && py -3.12 -m app.admin backup --out D:\UNO-kopijos"
```
Kopijas laikykite **kitame diske ar kitame kompiuteryje** nei programa. Senas kopijas (pvz. senesnes nei 30 dienų) trinkite rankiniu būdu arba atskira užduotimi. Kartą per mėnesį pabandykite `verify` ir atkūrimą į laikiną aplanką.

## 3. Atkūrimas

```
py -3.12 -m app.admin restore D:\UNO-kopijos\uno-backup-....zip --target C:\UNO\data
```
- Pirma patikrinamos visų failų kontrolinės sumos; sugadintas ar pakeistas archyvas atmetamas ir nieko nerašoma.
- Jei `--target\uno.db` jau yra, komanda sustoja. Tik tada, kai tikrai norite pakeisti esamus duomenis, pridėkite `--force`; senoji DB išsaugoma šalia kaip `uno.db.prieš-atkūrimą-<data>`.
- Prieš atkūrimą sustabdykite programą (uždarykite `paleisti_app.bat` langą).
- Absoliutūs įkeltų failų keliai DB perrašomi į naują vietą automatiškai.

## 4. Perkėlimas klientui

1. Mūsų pusėje: sustabdykite programą, padarykite `backup`, patikrinkite `verify`.
2. Klientui perduokite: programos aplanką (be `data\`) ir backup zip.
3. Kliento kompiuteryje atlikite 1 skyriaus 1-3 žingsnius (Python, `pip install`), tada:
   ```
   py -3.12 -m app.admin restore uno-backup-....zip --target C:\UNO\data
   ```
4. Naudotojai ir slaptažodžių maišos perkeliami kartu su DB. Jei reikia naujų naudotojų: `app.auth create-user`. Mūsų testinius naudotojus išjunkite ar pakeiskite slaptažodžius.
5. Paleiskite `paleisti_app.bat`, prisijunkite, patikrinkite, kad matosi įmonė, įkėlimai ir ankstesni skaičiavimai. Tada mūsų pusėje ištrinkite kliento duomenų kopijas (DB, `uploads`, zip) ir patvirtinkite raštu.
6. Kliente suplanuokite 2 skyriaus kasdienę kopiją.

## 5. Kokie duomenys asmeniniai ir kur jie laikomi

| Kas | Kur | Pastabos |
|---|---|---|
| Darbuotojų vardai, pavardės, asmens kodai, atlyginimai, darbo laiko apskaita, Sodros duomenys | `data\uno.db` ir įkelti failai `data\uploads\<įmonė>\<laikotarpis>\` | Tai asmens duomenys (BDAR); algos ir darbo laikas jautrūs. |
| Bankų išrašai, sąskaitos faktūros (kontrahentų pavadinimai, sąskaitų numeriai, ketinamos fizinių asmenų sumos) | tie patys `uploads` ir `uno.db` | Gali turėti fizinių asmenų duomenų. |
| Naudotojų vardai, slaptažodžių maišos, sesijos | `data\uno.db` (lentelės `users`, sesijos) | Slaptažodžiai saugomi tik kaip maišos. |
| Veiksmų žurnalas (kas, ką, kada) | `data\uno.db` (lentelė `audit`) | Būtinas atsekamumui. |
| Atsarginės kopijos | zip aplanke, kurį pasirinkote | Turi **visus** aukščiau išvardintus duomenis; saugokite kaip pačią DB (užrakintas diskas, ribota prieiga, geriausia šifruotas diskas BitLocker). |

Jokie duomenys nesiunčiami į išorines paslaugas; programa veikia lokaliai.

## 6. DPA (duomenų tvarkymo sutartis) pastaba

Kai klientas (įmonė) yra duomenų valdytojas, o mes tvarkome jo darbuotojų duomenis (diegimas, palaikymas, kopijos, duomenų importas), reikia pasirašyti duomenų tvarkymo sutartį pagal BDAR 28 straipsnį: tvarkymo paskirtis ir trukmė, duomenų kategorijos, saugumo priemonės, subtvarkytojai (jei yra), pagalba atsakant subjektams, duomenų grąžinimas ar ištrynimas pasibaigus darbams. Šis dokumentas nėra teisinė konsultacija; sutartį turi peržiūrėti klientas ar jo teisininkas. Kai programa veikia kliento kompiuteryje ir mes prieigos neturime, DPA gali būti reikalinga tik palaikymo sesijoms ar duomenų perdavimo momentu.
