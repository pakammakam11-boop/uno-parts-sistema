#!/usr/bin/env bash
# UNO atnaujinimas VPS: (neprivaloma git pull) + kopija prieš + perstatymas + migracijos + patikra.
#   sudo bash /opt/uno/uno-apskaita/deploy/update.sh [--no-backup]
# Naują kodą pirma nukopijuokite iš Windows PC (rsync, DIEGIMAS_VPS.md 4 žingsnis) arba, jei aplankas yra git
# klonas, skriptas pats padaro git pull --ff-only.
set -euo pipefail

UNO_HOME="${UNO_HOME:-/opt/uno}"
APP_DIR="$UNO_HOME/uno-apskaita"
MARSRUTAI_DIR_ABS="$UNO_HOME/marsrutai"
ENV_FILE="$UNO_HOME/.env"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$APP_DIR/deploy/docker-compose.yml")

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nKLAIDA: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Paleiskite su sudo"
[ -f "$ENV_FILE" ] || die "Nerasta $ENV_FILE: pirma paleiskite install.sh"
DO_BACKUP=1
[ "${1:-}" = "--no-backup" ] && DO_BACKUP=0

for repo in "$APP_DIR" "$MARSRUTAI_DIR_ABS"; do
    if [ -d "$repo/.git" ]; then
        say "git pull $repo"
        git -C "$repo" pull --ff-only
    fi
done

"${COMPOSE[@]}" config -q

if [ "$DO_BACKUP" -eq 1 ]; then
    say "Kopija prieš atnaujinimą"
    "${COMPOSE[@]}" run --rm backup once
fi

say "Ankstesnių vaizdų žyma (atsukimui): uno-app:prev, uno-marsrutai:prev"
docker tag uno-app:local uno-app:prev 2>/dev/null || true
docker tag uno-marsrutai:local uno-marsrutai:prev 2>/dev/null || true

ROLLBACK_HINT="Atsukimas: docker tag uno-app:prev uno-app:local; docker tag uno-marsrutai:prev uno-marsrutai:local; ${COMPOSE[*]} up -d --no-build (duomenys: deploy/backup-restore.md)"
fail_update() { printf '\n%s\n' "$ROLLBACK_HINT" >&2; die "$*"; }

say "Perstatymas ir paleidimas"
"${COMPOSE[@]}" up -d --build --remove-orphans --wait --wait-timeout 180 || {
    "${COMPOSE[@]}" ps || true
    fail_update "servisai nepasidarė sveiki per 180 s"
}

say "Migracijos"
# UNO SQLite (users/sessions/uploads) migruojama prisijungiant; HR ir apžvalgos lentelės (Postgres) kuriamos čia;
# Maršrutų mr_* lentelės kuriamos pirmos užklausos metu (create table if not exists).
"${COMPOSE[@]}" exec -T app python -c "from core.hr import db; db.get_engine(); print('HR schema OK')"
"${COMPOSE[@]}" exec -T app python -c "from app import auth; auth.connect().close(); print('UNO SQLite OK')"

say "Patikra"
"${COMPOSE[@]}" ps
# Visi aprašyti servisai turi veikti ir nebūti unhealthy / starting (tikrinama po --wait dar kartą).
for svc in $("${COMPOSE[@]}" config --services); do
    line="$("${COMPOSE[@]}" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}' "$svc" | head -n1)"
    state="$(printf '%s' "$line" | cut -d'|' -f2)"
    health="$(printf '%s' "$line" | cut -d'|' -f3)"
    if [ "$state" != "running" ] || [ "$health" = "unhealthy" ] || [ "$health" = "starting" ]; then
        fail_update "servisas $svc: būsena='${state:-nėra}', sveikata='${health:-nėra}'"
    fi
done
DOMAIN="$(grep -E '^DOMAIN=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
[ -n "$DOMAIN" ] || fail_update "nerastas DOMAIN $ENV_FILE faile"
curl -fsS -m 15 "https://$DOMAIN/healthz" || fail_update "HTTPS patikra nepavyko: https://$DOMAIN/healthz"
printf '\nAtnaujinta.\n'
docker image prune -f >/dev/null
