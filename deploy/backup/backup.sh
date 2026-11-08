#!/bin/sh
# Kasdienė kopija: pg_dump (uno, marsrutai) + UNO duomenų tūris (/data: uno.db per sqlite .backup, pašto PDF,
# tiekėjai, AI talpykla, gama). uno.db archyve vadinasi uno.db.snapshot (atkuriant pervadinti į uno.db).
# Saugoma /backups, paliekama BACKUP_KEEP naujausių kiekvieno tipo. Nepavykus dump / sqlite / tar žingsniui
# to tipo kopija nepublikuojama, senos nešalinamos, `once` baigiasi kodu 1 (loop tik žurnale rašo KLAIDA).
#   backup.sh loop   kasdien BACKUP_HOUR:00 (UTC konteinerio laiku), pirma kopija tik kitą BACKUP_HOUR
#   backup.sh once   viena kopija dabar (docker compose run --rm backup once)
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
DATA_DIR=${DATA_DIR:-/data}
KEEP=${BACKUP_KEEP:-14}
HOUR=${BACKUP_HOUR:-3}
DATABASES=${BACKUP_DATABASES:-"uno marsrutai"}

log() { echo "$(date -u +%FT%TZ) backup: $*"; }

prune() {
    # $1 = failų šablonas; paliekami $KEEP naujausi
    ls -1t $BACKUP_DIR/$1 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f -- "$old"; done
}

run_once() {
    # Kritinės komandos tikrinamos aiškiai (set -e čia neveikia, nes loop kviečia `run_once || ...`).
    # Kopija publikuojama (mv) ir senos šalinamos TIK po sėkmingo to tipo žingsnio; nepavykus lieka senos kopijos.
    ts=$(date -u +%Y%m%d-%H%M%S)
    failed=0
    mkdir -p "$BACKUP_DIR" || return 1
    for db in $DATABASES; do
        out="$BACKUP_DIR/pg-$db-$ts.dump"
        if pg_dump --format=custom --file="$out.part" "$db" && [ -s "$out.part" ] && mv "$out.part" "$out"; then
            log "pg_dump $db -> $out ($(du -h "$out" | cut -f1))"
            prune "pg-$db-*.dump"
        else
            log "KLAIDA: pg_dump $db nepavyko, senos kopijos paliktos"
            rm -f "$out.part"
            failed=1
        fi
    done

    if data_backup; then
        prune "data-*.tgz"
    else
        failed=1
    fi
    return "$failed"
}

data_backup() {
    stage=$(mktemp -d) || return 1
    out="$BACKUP_DIR/data-$ts.tgz"
    # Archyvuojami tik pavadinti failai (ne ".", ne laikino katalogo šaknis): atkuriant nekeičiamos katalogo teisės.
    if [ -f "$DATA_DIR/uno.db" ] && ! sqlite3 "$DATA_DIR/uno.db" ".backup '$stage/uno.db.snapshot'"; then
        log "KLAIDA: sqlite .backup nepavyko, senos kopijos paliktos"
        rm -rf "$stage"
        return 1
    fi
    set --
    for entry in "$DATA_DIR"/* "$DATA_DIR"/.[!.]*; do
        [ -e "$entry" ] || continue
        case "$(basename "$entry")" in uno.db|uno.db-wal|uno.db-shm|mail_incoming) continue ;; esac
        set -- "$@" "$(basename "$entry")"
    done
    if [ -f "$stage/uno.db.snapshot" ]; then
        set -- "$@" -C "$stage" uno.db.snapshot
    fi
    if tar -czf "$out.part" -C "$DATA_DIR" "$@" && [ -s "$out.part" ] && mv "$out.part" "$out"; then
        log "data -> $out ($(du -h "$out" | cut -f1))"
        rm -rf "$stage"
        return 0
    fi
    log "KLAIDA: data tar nepavyko, senos kopijos paliktos"
    rm -f "$out.part"
    rm -rf "$stage"
    return 1
}

seconds_until_next() {
    # UTC sekundės nuo paros pradžios iki BACKUP_HOUR:00 (be GNU date, tik aritmetika)
    since_midnight=$(( $(date -u +%s) % 86400 ))
    wait=$(( HOUR * 3600 - since_midnight ))
    if [ "$wait" -le 0 ]; then wait=$(( wait + 86400 )); fi
    echo "$wait"
}

case "${1:-loop}" in
    once) run_once || { log "KLAIDA: kopija nepavyko"; exit 1; } ;;
    loop)
        log "paleista: kasdien ${HOUR}:00 UTC, laikoma $KEEP kopijų"
        while true; do
            sleep "$(seconds_until_next)"
            run_once || log "KLAIDA: kopija nepavyko (senos kopijos paliktos), kita bandymas po paros"
        done ;;
    *) echo "naudojimas: backup.sh [loop|once]" >&2; exit 2 ;;
esac
