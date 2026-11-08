# UNO diegimas į Hostinger VPS (Ubuntu 24.04, Docker)

Kelias nuo „nupirktas VPS“ iki veikiančio `https://<domenas>`: apie 15 minučių darbo, be laukimo dėl DNS.
Sistema: UNO programa (FastAPI) + Maršrutai (kolegos Node programa) + Postgres + Caddy (automatinis HTTPS) + pašto procesas (worker, kas 15 min.) + kasdienės kopijos.

Visi `uno ...` komandos VPS yra trumpinys `docker compose --env-file /opt/uno/.env -f /opt/uno/uno-apskaita/deploy/docker-compose.yml ...` (sukuria `install.sh`).

## 0. Ko reikia

- Domenas arba subdomenas (pvz. `uno.uno-parts.lt`) ir prieiga prie jo DNS.
- Windows PC su PowerShell ir `ssh`/`scp` (įtaisyti Windows 11), Git.
- UNO repo (šaka su `deploy/`) ir kolegos Maršrutų repo (šaka `feat/uno-shell`).

## 1. Nupirkti VPS

Hostinger > VPS > **KVM 2** (2 vCPU, 8 GB RAM) arba didesnis. Operacinė sistema: **Ubuntu 24.04** (paprasta, ne „su Docker“ šablonas: `install.sh` pats įdiegia Docker). Nustatyti root slaptažodį arba SSH raktą ir užsirašyti **IP adresą**.

## 2. DNS

Domeno DNS: **A įrašas** `uno` (arba pasirinktas subdomenas) -> VPS IP, TTL 300. Patikra po kelių minučių:
```
nslookup uno.uno-parts.lt
```
Turi grąžinti VPS IP. (Be teisingo A įrašo Caddy negaus sertifikato.)

## 3. Pirmas prisijungimas

PowerShell:
```
ssh root@<VPS IP>
mkdir -p /opt/uno
exit
```
Rekomenduojama iškart įdėti savo SSH raktą (`ssh-copy-id root@<VPS IP>` arba Hostinger hPanel > SSH keys) ir vėliau išjungti slaptažodžių prisijungimą.

## 4. Nukopijuoti du aplankus iš Windows PC

Kopijuojamas tik **commit'intas** kodas (`git archive`): be `node_modules`, be `.env`, be tikrų duomenų, su LF eilučių pabaigomis skriptuose.

```powershell
# UNO (šaka su deploy/ aplanku)
cd C:\Users\Kompiuteris\Desktop\Clients\salvaras-apskaita\uno-apskaita
git archive --format=tar.gz -o $env:TEMP\uno-apskaita.tar.gz feat/deploy-vps     # po merge: master
# Maršrutai
cd C:\Users\Kompiuteris\Desktop\Clients\salvaras-apskaita\kolegos-routes\routes-main
git archive --format=tar.gz -o $env:TEMP\marsrutai.tar.gz feat/uno-shell

scp $env:TEMP\uno-apskaita.tar.gz $env:TEMP\marsrutai.tar.gz root@<VPS IP>:/opt/uno/
ssh root@<VPS IP> "cd /opt/uno && rm -rf uno-apskaita marsrutai && mkdir uno-apskaita marsrutai && tar xzf uno-apskaita.tar.gz -C uno-apskaita && tar xzf marsrutai.tar.gz -C marsrutai && rm *.tar.gz"
```
Jei Windows turi `rsync` (Git Bash / WSL), galima ir taip (be `.git`, `node_modules`, `data/*.db`):
```
rsync -az --delete --exclude .git --exclude node_modules --exclude 'data/uno.db*' --exclude 'data/hr.db*' --exclude 'data/uploads' --exclude vault \
  /c/Users/Kompiuteris/Desktop/Clients/salvaras-apskaita/uno-apskaita/ root@<VPS IP>:/opt/uno/uno-apskaita/
rsync -az --delete --exclude .git --exclude node_modules --exclude .vercel --exclude 'data/scenarios' --exclude out \
  /c/Users/Kompiuteris/Desktop/Clients/salvaras-apskaita/kolegos-routes/routes-main/ root@<VPS IP>:/opt/uno/marsrutai/
```
(`rsync` iš Windows aplanko gali atvežti CRLF skriptuose: tada VPS paleisti `sed -i 's/\r$//' /opt/uno/uno-apskaita/deploy/*.sh /opt/uno/uno-apskaita/deploy/*/*.sh`. `git archive` šios problemos neturi.)

## 5. Paleisti diegimą

```
ssh root@<VPS IP>
bash /opt/uno/uno-apskaita/deploy/install.sh
```
Pirmas paleidimas įdiegia Docker, ugniasienę (ufw: tik 22, 80, 443), automatinius saugumo atnaujinimus, sukuria `/opt/uno/.env` su sugeneruotais slaptažodžiais ir **sustoja**. Užpildyti:
```
nano /opt/uno/.env        # būtina: DOMAIN, ACME_EMAIL
bash /opt/uno/uno-apskaita/deploy/install.sh
```
Antras paleidimas pastato vaizdus (~5-10 min.), paleidžia visus konteinerius ir ištikrina `https://<domenas>/healthz`.

## 6. Pirmas administratorius

```
uno exec app python -m app.admin init --company "<įmonės pavadinimas>"
uno exec -it app python -m app.auth create-user --company 1 --username <vardas> --role admin
```
Slaptažodžio (min. 12 simbolių) klaus komanda. Tada naršyklėje `https://<domenas>/app` ir prisijungti. Papildomi naudotojai: tas pats su `--role accountant` (buhalterė) arba `manager` (vadovas).

Svarbu: `/apskaita` (Sąskaitos iš pašto) ir `/marsrutai/` atsidaro tik prisijungus. Pirma prisijungti per `/app`, tada atidaryti kitus adresus.

## 7. Gmail programos slaptažodis (<SASKAITU_PASTAS>@gmail.com)

1. Prisijungti prie `<SASKAITU_PASTAS>@gmail.com`, įjungti 2 žingsnių patvirtinimą: https://myaccount.google.com/security
2. Atidaryti https://myaccount.google.com/apppasswords, pavadinimas `UNO VPS`, **Create**. Nukopijuoti 16 simbolių kodą.
3. VPS: `nano /opt/uno/.env`, eilutė `UNO_IMAP_PASSWORD=<16 simbolių be tarpų>`, `UNO_IMAP_USER=<SASKAITU_PASTAS>@gmail.com`.
4. Pritaikyti ir patikrinti prisijungimą:
   ```
   uno up -d app worker
   uno run --rm worker python -m tools.mail_poll ; echo "kodas=$?"
   ```
   `kodas=0` = pašto importas veikia (0 PDF yra normalu, kol neatėjo sąskaitų). `kodas=1` = prisijungimo klaida (neteisingas slaptažodis, 2FA neįjungtas). `kodas=2` = kintamieji neužpildyti.

IMAP pasiekia tik skaitymo režimu (laiškai nežymimi ir netrinami).

**Sąskaitos tik su nuoroda (pvz. Agnum).** Laiškai be PDF priedo tikrinami dėl nuorodų; sekamos tik https:443 nuorodos į domenus iš `UNO_INVOICE_LINK_DOMAINS` (kableliais, numatyta `agnum.lt`). URL kanonizuojamas (backslash, `@`, ne-443 prievadai, IP adresai atmetami). Chromium veikia **tik atskirame konteineryje `linkfetch`**, ne worker'yje:

- `linkfetch` (tas pats vaizdas, `python -m tools.linkfetch_server`) neturi jokių paslapčių (nei DB, nei IMAP, nei Google; tik `LINKFETCH_TOKEN`), yra tik tinkluose `egress` (internetas) ir `fetchnet` (vidinis, be interneto; jame tik `app`, `worker`, `linkfetch`), todėl nepasiekia postgres ar Maršrutų. Read-only šaknis, `/tmp` tmpfs 256 MB, `mem_limit 768m`, `pids_limit 256`, `cpus 1`, `cap_drop ALL`, Playwright seccomp profilis (`chromium-seccomp.json`, Chromium smėlio dėžė be SYS_ADMIN), `init: true`, `restart: unless-stopped`.
- Atmintį, procesų skaičių ir CPU riboja konteinerio cgroup (S13). Vienu metu vykdoma viena užklausa; kita gauna 503.
- Bandymas ilgesnis nei 60 s: watchdog kviečia `os._exit(70)`, konteineris sustoja, Docker jį paleidžia iš naujo ir žūsta VISI palikuonys, įskaitant atsiskyrusius Chromium procesus (S11/S14 garantija; PID sąrašų nereikia). Tai matysite kaip `linkfetch` perkrovimą (`uno ps`, `uno logs linkfetch`), o nuoroda baigsis įspėjimu.
- Visas išėjimas iš `linkfetch` eina per vidinį CONNECT tarpinį serverį: leidžiami tik sąrašo domenai ir tik viešieji IP (privatūs, loopback, 169.254/16, CGNAT atmetami; jungiamasi prie patikrinto IP), PDF iki 20 MB ir tik `%PDF-`.
- Kiekvienas kvietimas gauna likusį laiško laiko ir baitų biudžetą (`max_seconds`, `max_bytes`); `linkfetch` taiko mažesnį iš savo ir gauto limito ir grąžina sunaudotus baitus (`X-Bytes-Used` / `bytes_used`), kuriuos worker nuskaičiuoja ir nesėkmės atveju. Visam sidecar atsakymui (antraštės + kūnas) galioja vienas absoliutus terminas, klaidų kūnas skaitomas iki 4 KB.
- `app` lieka `fetchnet` tinkle: Sąskaitų skilties mygtukas „Importuoti iš pašto“ vykdo importą pačiame `app` (`services.run_invoices_mail`), o ne per worker. Perkelti jį į worker eilę yra didesnis pakeitimas nei šio rato apimtis; rizika maža (`fetchnet` internal, tik app + worker + linkfetch).
- `app` ir `worker` kreipiasi į `http://linkfetch:8099/fetch` su `Authorization: Bearer $LINKFETCH_TOKEN`. Tokeną sugeneruoja `install.sh` (taip pat pridės į jau esantį `.env`); be jo `docker compose` nepasileis. Vietinėje Windows aplinkoje (be `LINKFETCH_URL`) naudojamas senas vaiko proceso variantas.

Nepavykus, Sąskaitų skiltyje atsiranda įspėjimas „Sąskaitos nuorodos parsisiųsti nepavyko“ (siuntėjo domenas, laiško nr., nuorodos domenas): tą sąskaitą atsisiųskite ranka. Kitų domenų nuorodos ignoruojamos tyliai.

Patikra po `docker compose build` ir `up -d` (Docker šioje mašinoje nebuvo, todėl čia nepaleista; vykdyti VPS):
```
uno ps linkfetch                    # Up (healthy)
uno exec worker python -c "import os,httpx; print(httpx.get('http://linkfetch:8099/healthz').text)"   # ok
uno exec linkfetch python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(chromium_sandbox=True); print('chromium', b.version); b.close(); p.stop()"
uno exec linkfetch env | grep -ciE 'IMAP|POSTGRES|GOOGLE|ANTHROPIC|DATABASE'   # turi būti 0
uno exec linkfetch python -c "import socket; socket.create_connection(('postgres',5432),2)"   # turi MESTI klaidą (tinklas atskirtas)
```
Klaida „No usable sandbox“ = seccomp profilis nepritaikytas (`docker inspect` -> SecurityOpt) arba branduolys neleidžia user namespaces (`sysctl kernel.unprivileged_userns_clone`).

## 8. Liutauro persiuntimo taisyklės į <SASKAITU_PASTAS>@gmail.com

Tikslas: sąskaitos iš Intercars ir kitų tiekėjų patenka į `<SASKAITU_PASTAS>@gmail.com` automatiškai. Persiųsti reikia tik laiškus su PDF sąskaitomis (mažiau asmens duomenų).

**Outlook (@intercars.eu)** (Outlook internete, outlook.office.com):
1. Dešinėje viršuje krumpliaratis **Settings** > **Mail** > **Rules** > **Add new rule**.
2. Pavadinimas: `UNO sąskaitos`.
3. **Add a condition**: *Message has attachments* (jei galima, papildomai *From* ir pasirinkti Intercars adresus arba *Subject includes* `sąskaita`/`invoice`).
4. **Add an action**: **Forward to** (ne *Redirect*) > `<SASKAITU_PASTAS>@gmail.com`.
5. Pažymėti *Stop processing more rules* tik jei yra kitų taisyklių, kurių tai neturi paveikti. **Save**.
6. Jei įmonės IT blokuoja automatinį persiuntimą į išorę, taisyklė tyliai neveiks. Tada IT administratorius turi leisti išorinį persiuntimą šiam pašto adresui (Exchange admin: *Outbound spam filter policy > Automatic forwarding*).

**Gmail** (jei dalis sąskaitų ateina į Gmail):
1. Gmail > krumpliaratis > **See all settings** > **Forwarding and POP/IMAP** > **Add a forwarding address** > `<SASKAITU_PASTAS>@gmail.com`.
2. Google nusiųs patvirtinimo laišką į `<SASKAITU_PASTAS>@gmail.com`: atidaryti jį toje dėžutėje ir paspausti patvirtinimo nuorodą (kodą matysite prisijungę prie tos dėžutės per naršyklę).
3. **Filters and Blocked Addresses** > **Create a new filter**: *Has the words* `has:attachment filename:pdf` (arba *From* tiekėjo adresas) > **Create filter** > pažymėti **Forward it to** `<SASKAITU_PASTAS>@gmail.com` > **Create filter**.

Patikra: pasiųsti sau bandomąją sąskaitą su PDF per tą dėžutę ir žiūrėti 12 žingsnį.

## 9. Google raktas (Vertex AI laukams ir Maršrutams)

AI laukų ištraukimas VPS veikia per **Google Vertex AI (Gemini)**, ES daugiaregionis apdorojimas. Naudojamas tas pats service account JSON, kaip ir Maršrutų Google Route Optimization.

Komandos vykdomos Pauliaus kompiuteryje (`gcloud`), **ne per `install.sh`**. Pavyzdys projektui `uno-marsrutai-8068` (patikrinti projekto ID ir SA el. paštą per `gcloud iam service-accounts list`):
```
gcloud services enable aiplatform.googleapis.com --project=uno-marsrutai-8068
gcloud projects add-iam-policy-binding uno-marsrutai-8068 ^
  --member="serviceAccount:marsrutai-demo@uno-marsrutai-8068.iam.gserviceaccount.com" ^
  --role="roles/aiplatform.user"
gcloud iam service-accounts keys create uno-vps-key.json ^
  --iam-account="marsrutai-demo@uno-marsrutai-8068.iam.gserviceaccount.com"
```
(`^` yra CMD eilutės tęsinys; PowerShell naudoja `` ` ``.) Esamas Vercel raktas nematomas, todėl VPS kuriamas naujas raktas. Švaresnis variantas: atskiras SA `uno-vps` su abiem rolėmis `roles/aiplatform.user` ir `roles/routeoptimization.editor`.

Raktą paversti base64 viena eilute:
```
[Convert]::ToBase64String([IO.File]::ReadAllBytes("uno-vps-key.json"))     # PowerShell
base64 -w0 uno-vps-key.json                                               # Linux / Git Bash
```
VPS `.env`: `GOOGLE_SA_KEY_B64=<ta eilutė>`, `UNO_AI_PROVIDER=vertex`, `GOOGLE_CLOUD_PROJECT=uno-marsrutai-8068` (arba palikti tuščią: imama iš rakto). Tada `uno up -d`. JSON failą po to ištrinti iš Windows aplanko Atsisiuntimai.

**Modelis ir regionas** (patikrinta Google dokumentacijoje 2026-11-07): `gemini-3.8-flash` (GA nuo 2026-09-02, nutraukimas ne anksčiau kaip 2027-09-02). Palaikomi `global`, daugiaregionis `us` ir `eu`. Todėl numatytas `UNO_AI_LOCATION=eu` (endpoint `aiplatform.eu.rep.googleapis.com`, ML apdorojimas ES). Atskiras `europe-west1` šiam modeliui dokumentacijoje nenurodytas: jei Google jį pridės, užtenka nustatyti `UNO_AI_LOCATION=europe-west1`. Kitas modelis: `UNO_AI_MODEL=<id>`.

**Kodėl ne nemokamas Gemini API (AI Studio raktas):** nemokamame lygyje Google gali naudoti užklausas ir atsakymus produkto gerinimui, ir nėra duomenų tvarkymo sutarties (DPA). Sąskaitose yra tiekėjų ir kartais asmenų duomenų, todėl kode toks kelias neįgyvendintas: tik Vertex (Google Cloud sąlygos, DPA) arba Anthropic API.

## 10. Alternatyva: Anthropic API

`.env`: `UNO_AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=<raktas iš console.anthropic.com>`; modelis numatytas `claude-sonnet-5-5` (`UNO_AI_MODEL` keičia). `uno up -d`. Anthropic API pagal komercines sąlygas neapmoko modelių ant klientų duomenų, bet apdorojimas vyksta ne ES: prieš naudojant su tikromis sąskaitomis patvirtinti su Liutauru.

AI visai išjungti: `UNO_MAIL_AI=0` (tada nieko į išorę nesiunčiama, sąskaitos skaitomos tik paprastu parseriu).

## 11. Personalo laiškai (Resend), nebūtina

`/hr` ir `/` (apžvalga) prisijungimas gamyboje yra per el. pašto nuorodą (`HR_DEMO=0`). Laiškai išsiunčiami tik kai nustatyti ir `RESEND_API_KEY`, ir `HR_MAIL_ALLOWLIST` (gavėjų sąrašas kableliais), o `HR_MAIL_FROM` domenas patvirtintas Resend. Kol to nėra, prisijungimo nuorodos nerodomos niekur (gamyboje jos nesaugomos net sistemos dėžutėje; HR ir kiti svetimų nuorodų niekada nemato), todėl į `/hr` ir `/` neįmanoma prisijungti, kol nesukonfigūruotas Resend. `HR_DEMO=1` leidžia prisijungti pasirinkus personą be slaptažodžio: naudoti tik trumpai demonstracijai, ne ilgam.

## 12. Kaip įrodyti, kad veikia

Visos patikros su rezultatu, kurį turite pamatyti:

| # | Komanda / veiksmas | Turi būti |
|---|---|---|
| 1 | `curl -fsS https://<domenas>/healthz` | `{"status":"ok"}` |
| 2 | `curl -sI https://<domenas>/ \| grep -i strict-transport` | `strict-transport-security` antraštė |
| 3 | `curl -s -o /dev/null -w '%{http_code}\n' https://<domenas>/marsrutai/` (be prisijungimo) | `302` (nukreipia į `/app`) |
| 4 | `curl -s -o /dev/null -w '%{http_code}\n' https://<domenas>/api/overview` (be prisijungimo) | `401` |
| 5 | `uno ps` | visi konteineriai `running`, `app`, `worker`, `marsrutai`, `postgres` su `healthy` |
| 6 | Naršyklėje prisijungti `/app`, tada atidaryti `/marsrutai/` | Maršrutų dashboard'as, `…/marsrutai/api/status` grąžina `{"googleConfigured":true}` |
| 7 | AI patikra: `uno exec app python -c "from core import invoice_ai as a; print(a.provider(), a._call_claude('Sąskaita Nr. T-1, 2026-11-01. Pardavėjas UAB Testas, įm. k. 123456789. Suma be PVM 10.00, PVM 2.10, viso 12.10 EUR'))"` | `vertex {'number': 'T-1', ...}` (ne `None`) |
| 8 | **Pašto kelias:** iš Outlook (pagal 8 žingsnio taisyklę) arba iš telefono pasiųsti laišką su bet kokia PDF sąskaita į `<SASKAITU_PASTAS>@gmail.com` | per ≤15 min.: `uno logs --tail=20 worker` rodo `Pašto importas: N PDF ...` |
| 9 | Prisijungus atidaryti `https://<domenas>/apskaita` > **Sąskaitos** (perkrauti puslapį) | sąskaita yra sąraše (nežinomam tiekėjui: radinys „Nežinomas tiekėjas“ ir mygtukas „Sukurti tiekėją“) |
| 10 | `uno run --rm backup once && ls -lh /opt/uno/backups` | trys nauji failai (2 × `.dump`, 1 × `.tgz`) |
| 11 | `reboot`, po 2 min. `curl -fsS https://<domenas>/healthz` | vėl `ok` (konteineriai `restart: unless-stopped`) |

Jei 8 žingsnis nepavyksta: `uno logs --tail=50 worker`. Dažniausios priežastys: neteisingas programos slaptažodis, Outlook persiuntimą blokuoja IT, laiške nėra PDF priedo.

## 13. Atnaujinimas ir atsukimas

Atnaujinimas: nukopijuoti naują kodą (4 žingsnis), tada
```
bash /opt/uno/uno-apskaita/deploy/update.sh
```
Skriptas pirma padaro kopiją, pažymi senus vaizdus (`uno-app:prev`, `uno-marsrutai:prev`), perstato, sukuria schemas ir patikrina `/healthz`.

**Atsukimas (rollback):**
```
docker tag uno-app:prev uno-app:local
docker tag uno-marsrutai:prev uno-marsrutai:local
uno up -d --no-build
```
Jei pasikeitė duomenys, kuriuos reikia grąžinti: atkūrimas iš `/opt/uno/backups` (`backup-restore.md`).

## 14. Kainos (orientacinės, patikrinti prieš pirkimą)

| Eilutė | Kaina | Pastaba |
|---|---|---|
| Hostinger VPS KVM 2 (2 vCPU, 8 GB) | apie 8-12 €/mėn. | priklauso nuo termino (12/24 mėn. pigiau); patikrinti hPanel kainą |
| Domenas | apie 10-15 €/metus | arba subdomenas esamam domenui: 0 € |
| HTTPS sertifikatas (Let's Encrypt) | 0 € | automatinis per Caddy |
| Vertex AI Gemini Flash (sąskaitų laukai) | centai per mėnesį | keli šimtai sąskaitų per mėn.; pagal Google kainoraštį |
| Google Route Optimization (Maršrutai) | pagal siuntą | „Mock“ režimas nemokamas; „Google API“ mokamas už kiekvieną optimizavimą |
| Kopijos už VPS ribų | 0-5 €/mėn. | Hostinger kopijos priklauso nuo plano; B2/R2 pigu |
| Resend (laiškai) | 0 € | nemokamas lygis iki kelių tūkst. laiškų |

## 15. Atviri klausimai

1. **GAMA SQL iš VPS.** UNO serveris `<RIVILES_SERVERIS>` iš išorės atveria tik 3389 (RDP) ir 5985 (WinRM), o SQL (1433) ne. Dabartinis `tools/gama_snapshot.ps1` yra Windows PowerShell per WinRM ir VPS (Linux) neveikia, todėl `/app` „GAMA patikra“ VPS neįjungta (`UNO_GAMA_SNAPSHOT_CMD` tuščias). Variantai (neįgyvendinta):
   - **A. Tailscale** abiejose pusėse (VPS ir Rivilės serveris), SQL prieinamas tik per privatų tinklą; snapshot'ą perrašyti į Python (`pyodbc`/`pymssql`) arba paleisti WinRM per tailnet. Reikia UNO leidimo diegti programą serveryje.
   - **B. Tik skaitymo SQL naudotojas** (`db_datareader` tik reikalingoms lentelėms) + įjungtas TCP 1433 (SQL Express pagal nutylėjimą išjungtas) + Windows ugniasienė leidžia tik VPS IP + šifruotas ryšys. Tada reikia tik Python SQL skaitytuvo.
   Rekomendacija: A (mažiau paviršiaus), sprendimą priima UNO IT.
2. **`UNO_DEMO=1` kaip gyvas režimas.** „Sąskaitos iš pašto“ šiuo metu gyvena apskaitos (demo) skiltyje `/apskaita`, kurioje kitos skiltys naudoja išgalvotus demo duomenis. VPS ji uždaryta už prisijungimo (`UNO_DEMO_AUTH=1`). Gamybinis `/app` sąskaitų skilties dar neturi.
3. **Maršrutai neturi savo prisijungimo.** Apsaugoti Caddy `forward_auth` per UNO sesiją (`/_gate`). Tiesioginio prievado (8787) iš išorės nėra. Maršrutų „← UNO“ nuoroda veda į `/`.
4. **Pašto procesas ir programa du procesai.** Worker parsisiunčia PDF į bendrą tūrį, programa perskaito po sėkmingo importo. Jei tuo metu vyksta mygtuko „Importuoti iš pašto“ paspaudimas, rezultatas gali trumpam būti nepilnas (kitas ciklas ištaiso). Bendras failų užraktas neįgyvendintas.
5. **Vaizdai prisegti pagal tagą, ne digest.** (`python:3.12.8-slim-bookworm`, `node:22.12-bookworm-slim`, `postgres:16.6-alpine`, `caddy:2.8.4-alpine`.) Digest prisegimas: `docker buildx imagetools inspect <vaizdas>`.
6. **Nepatikrinta realiai (nebuvo Docker / VPS).** Žr. pridėtą patikros sąrašą 12 žingsnyje: Docker build, Caddy sertifikatas, `forward_auth`, Vertex iškvietimas su tikru raktu, Hostinger ugniasienė.
7. **Asmens duomenys.** Sąskaitų tekstas iš PDF siunčiamas į Vertex (ES) tik kai `UNO_MAIL_AI=1`. Prieš gyvą paleidimą patvirtinti su Liutauru ir turėti Google Cloud DPA sutikimą.
8. **SSH apsauga.** Skriptas nekeičia SSH konfigūracijos: po pirmo prisijungimo įdėti raktą ir išjungti `PasswordAuthentication`.
