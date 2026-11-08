# UNO VPS: atsarginės kopijos ir atkūrimas

Komanda `uno` = `docker compose` su teisingu `.env` ir compose failu (ją sukuria `install.sh`).

## Ką daro `backup` konteineris

Kasdien `BACKUP_HOUR`:00 UTC (numatyta 03:00 UTC, Lietuvoje 05:00 / 06:00) į `BACKUP_DIR` (numatyta `/opt/uno/backups`):

| Failas | Turinys |
|---|---|
| `pg-uno-<data>.dump` | Postgres DB `uno`: personalas (HR), apžvalga (dashboard) |
| `pg-marsrutai-<data>.dump` | Postgres DB `marsrutai`: Maršrutų scenarijai ir planai |
| `data-<data>.tgz` | Tūris `/data`: nuosekli `uno.db` kopija (`uno.db.snapshot`, naudotojai, DU kontrolės įkėlimai), pašto PDF, `suppliers.json`, AI talpykla, `gama/` |

Laikoma `BACKUP_KEEP` (14) naujausių kiekvieno tipo. Nepavykus `pg_dump` / sqlite / `tar` žingsniui to tipo nauja kopija nepublikuojama ir senų kopijų netrinama; `backup once` baigiasi kodu 1, o ciklas žurnale rašo `KLAIDA` (`uno logs backup`), tad žurnalą verta stebėti. Kopija iškart: `uno run --rm backup once`.
Patikra: `ls -lh /opt/uno/backups` ir `uno logs --tail=20 backup`.

Kopijos yra tame pačiame VPS diske, todėl nuo disko praradimo ar VPS ištrynimo jos neapsaugo. Reikia bent vienos kopijos už VPS ribų.

## Kopija už VPS ribų (rekomenduojama)

Variantai (nuo paprasčiausio):

1. **Hostinger VPS momentinės kopijos (snapshot / automatinės kopijos)**: įjungti hPanel > VPS > Backups. Apima visą diską, bet priklauso nuo Hostinger plano ir atkuriama tik per jų skydelį.
2. **rclone į debesį** (Backblaze B2, Cloudflare R2, Google Drive, S3): `apt install rclone`, `rclone config` (vienas nuotolinis saugojimas, pvz. `offsite`), tada kasdien po kopijos (pvz. 03:30 UTC) crontab:
   ```
   30 3 * * * rclone sync /opt/uno/backups offsite:uno-backups --log-file=/var/log/uno-rclone.log
   ```
   Naudoti `sync` tik su atskiru kibiru, kuriame nėra kitų failų. Kopijos turi asmens duomenų (darbuotojai, sąskaitos): saugojimas ES regione, kibiro prieiga tik Pauliui, geriausia `rclone crypt`.
3. **scp / rsync į kitą kompiuterį** (pvz. Pauliaus namų serveris): `rsync -a root@<VPS>:/opt/uno/backups/ D:\UNO-kopijos\`.

Reikia kas mėnesį išbandyti atkūrimą į laikiną vietą (žemiau).

## Atkūrimas

Visų atvejų pradžia: sustabdyti programas, DB paliekant veikiančią.

```
uno stop app worker marsrutai caddy
```

### Postgres (HR ir apžvalga: `uno`; Maršrutai: `marsrutai`)

```
# 1) išvalyti ir sukurti DB iš naujo
uno exec postgres psql -U uno -d postgres -c "DROP DATABASE IF EXISTS uno WITH (FORCE)"
uno exec postgres psql -U uno -d postgres -c "CREATE DATABASE uno OWNER uno"
# 2) įkelti kopiją
cat /opt/uno/backups/pg-uno-YYYYMMDD-HHMMSS.dump | uno exec -T postgres pg_restore -U uno -d uno --no-owner
```
Maršrutams tas pats su `marsrutai` ir `pg-marsrutai-*.dump`.

### UNO duomenys (`/data`: uno.db, pašto PDF, tiekėjai, AI talpykla)

```
# 3) išskleisti į laikiną aplanką (kaip root) ir pažiūrėti; archyve tik pavadinti failai, be "." šaknies
mkdir -p /tmp/uno-restore && tar -xzf /opt/uno/backups/data-YYYYMMDD-HHMMSS.tgz -C /tmp/uno-restore
ls -la /tmp/uno-restore
# 4) perkelti į tūrį (programa sustabdyta). Pirma pasidaryti dabartinio turinio kopiją.
#    Vykdoma kaip root (--user root), kad nuskaitytų /restore; gale grąžinama savininko teisė UID 10001 (uno).
uno run --rm --no-deps --user root -v /tmp/uno-restore:/restore:ro --entrypoint sh app -c   'cp -a /restore/. /data/ && mv -f /data/uno.db.snapshot /data/uno.db && rm -f /data/uno.db-wal /data/uno.db-shm    && chown -R 10001:10001 /data'
rm -rf /tmp/uno-restore
```
(`chown -R 10001:10001 /data` būtinas: programa veikia kaip `uno` (UID 10001) ir kitaip neįrašytų į tūrį.)

### Paleisti ir patikrinti

```
uno up -d
curl -fsS https://<domenas>/healthz
```
Prisijungti per `/app`, patikrinti naudotojus, atidaryti `/marsrutai/`, `/hr`.

## Visiška avarija (naujas VPS)

1. `install.sh` naujame VPS (DIEGIMAS_VPS.md), bet po pirmo `.env` sukūrimo **nepaleisti** `up`: nukopijuoti seną `.env` (slaptažodžiai turi sutapti) į `/opt/uno/.env`.
2. `uno up -d postgres`, atkurti `pg-*.dump` ir `data-*.tgz` kaip aukščiau, tada `uno up -d --build`.
3. Pakeisti DNS A įrašą į naują IP (Caddy gaus naują sertifikatą pats).

`.env` (slaptažodžiai, raktai) nėra kopijuojamas automatiškai: laikyti jo kopiją slaptažodžių tvarkyklėje.

## Be docker: UNO integruotas įrankis

Programos konteineryje veikia ir senasis įrankis (DB + įkelti failai su kontrolinėmis sumomis):
`uno exec app python -m app.admin backup --out /data/backups` ir `... verify <zip>`. Jis nepakeičia kasdienio `backup` konteinerio, tik papildo prieš didesnį pakeitimą.
