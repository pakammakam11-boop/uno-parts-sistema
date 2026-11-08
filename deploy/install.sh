#!/usr/bin/env bash
# UNO diegimas į švarų Ubuntu 24.04 VPS (Hostinger). Idempotentiškas: galima paleisti pakartotinai.
#   sudo bash /opt/uno/uno-apskaita/deploy/install.sh
# Prieš tai iš Windows PC nukopijuoti du aplankus (žr. DIEGIMAS_VPS.md, 4 žingsnis):
#   /opt/uno/uno-apskaita   (šis repo)      /opt/uno/marsrutai   (kolegos Maršrutų repo)
set -euo pipefail

UNO_HOME="${UNO_HOME:-/opt/uno}"
APP_DIR="$UNO_HOME/uno-apskaita"
MARSRUTAI_DIR_ABS="$UNO_HOME/marsrutai"
COMPOSE_FILE="$APP_DIR/deploy/docker-compose.yml"
ENV_FILE="$UNO_HOME/.env"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nKLAIDA: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Paleiskite su sudo: sudo bash $0"

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
    echo "Dėmesio: testuota Ubuntu 24.04, rasta ${PRETTY_NAME:-nežinoma}. Tęsiama." >&2
fi

say "1/7 Paketai (curl, ufw, unattended-upgrades, openssl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg ufw unattended-upgrades openssl rsync

say "2/7 Docker Engine + compose plugin (oficialus Docker apt saugykla)"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker compose version

say "3/7 Ugniasienė (ufw): leidžiama tik SSH, 80, 443"
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')"
SSH_PORT="${SSH_PORT:-22}"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
[ "$SSH_PORT" = "22" ] || ufw allow "${SSH_PORT}/tcp"
ufw allow 80/tcp
ufw allow 443
ufw --force enable
ufw status verbose | head -20

say "4/7 Automatiniai saugumo atnaujinimai"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

say "5/7 Katalogai ($UNO_HOME)"
mkdir -p "$UNO_HOME" "$UNO_HOME/backups"
chmod 750 "$UNO_HOME"
[ -f "$COMPOSE_FILE" ] || die "Nerasta $COMPOSE_FILE. Nukopijuokite repo iš Windows PC:
  scp -r <UNO repo>  root@<VPS IP>:$APP_DIR
  (rsync komandos: deploy/DIEGIMAS_VPS.md, 4 žingsnis)"
[ -f "$MARSRUTAI_DIR_ABS/package.json" ] || die "Nerasta $MARSRUTAI_DIR_ABS/package.json. Nukopijuokite kolegos Maršrutų repo į $MARSRUTAI_DIR_ABS (be node_modules)."

# Patogi komanda `uno` = docker compose su teisingu .env ir compose failu (uno ps, uno logs -f worker, uno exec app ...)
cat > /usr/local/bin/uno <<WRAP
#!/bin/sh
exec docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "\$@"
WRAP
chmod 0755 /usr/local/bin/uno

say "6/7 Konfigūracija ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
    cp "$APP_DIR/deploy/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    for key in POSTGRES_PASSWORD HR_POSTBACK_SECRET LINKFETCH_TOKEN; do
        sed -i "s|^${key}=CHANGE_ME_AUTO\$|${key}=$(openssl rand -hex 24)|" "$ENV_FILE"
    done
    echo "Sukurtas $ENV_FILE su sugeneruotais slaptažodžiais (POSTGRES_PASSWORD, HR_POSTBACK_SECRET, LINKFETCH_TOKEN)."
fi
# Esamas .env be LINKFETCH_TOKEN (diegimas prieš naršyklės atskyrimą): sugeneruoti ir pridėti
if ! grep -qE '^LINKFETCH_TOKEN=.+' "$ENV_FILE" || grep -qE '^LINKFETCH_TOKEN=CHANGE_ME_AUTO$' "$ENV_FILE"; then
    sed -i '/^LINKFETCH_TOKEN=/d' "$ENV_FILE"
    echo "LINKFETCH_TOKEN=$(openssl rand -hex 24)" >> "$ENV_FILE"
    echo "Sugeneruotas LINKFETCH_TOKEN ($ENV_FILE)."
fi
chmod 600 "$ENV_FILE"

get() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2-; }
DOMAIN="$(get DOMAIN)"
ACME_EMAIL="$(get ACME_EMAIL)"
if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "uno.example.lt" ] || [ -z "$ACME_EMAIL" ] || [ "$ACME_EMAIL" = "paulius@example.lt" ]; then
    cat <<MSG

SUSTOTA: užpildykite $ENV_FILE ir paleiskite šį skriptą dar kartą.
  nano $ENV_FILE
Būtina: DOMAIN (DNS A įrašas turi rodyti į šį VPS), ACME_EMAIL.
Vėliau galima pridėti: UNO_IMAP_PASSWORD, GOOGLE_SA_KEY_B64, RESEND_API_KEY (žr. DIEGIMAS_VPS.md).
MSG
    exit 0
fi

say "7/7 docker compose up -d --build (pirmas kartas ~5-10 min.)"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" config -q
"${COMPOSE[@]}" up -d --build

echo "Laukiama, kol konteineriai taps sveiki (iki 5 min.)..."
for _ in $(seq 1 60); do
    unhealthy="$("${COMPOSE[@]}" ps --format '{{.Service}}|{{.Health}}|{{.State}}' | awk -F'|' '$2 == "starting" || $3 != "running" {print $1}' | tr '\n' ' ')"
    [ -z "${unhealthy// /}" ] && break
    sleep 5
done

"${COMPOSE[@]}" ps
echo
if curl -fsS -m 15 "https://$DOMAIN/healthz"; then
    printf '\n\nVeikia: https://%s\nToliau: DIEGIMAS_VPS.md, 7 žingsnis (pirmas administratorius).\n' "$DOMAIN"
else
    printf '\nHTTPS dar neatsako. Dažniausia priežastis: DNS A įrašas dar nepasklido arba 80/443 uždaryti.\nŽurnalai: docker compose --env-file %s -f %s logs --tail=50 caddy\n' "$ENV_FILE" "$COMPOSE_FILE"
fi
