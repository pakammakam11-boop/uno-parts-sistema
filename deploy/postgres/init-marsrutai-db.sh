#!/bin/sh
# Vykdoma tik pirmą kartą kuriant Postgres duomenų katalogą. Antra DB Maršrutams (mr_* lentelės).
# Jei pg_data jau buvo sukurtas anksčiau:  docker compose exec postgres createdb -U uno marsrutai
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE DATABASE marsrutai OWNER $POSTGRES_USER"
