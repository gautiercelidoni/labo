#!/bin/sh
# Point d'entrée du conteneur : attend PostgreSQL, applique les migrations si demandé, lance la commande.
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  url="${DATABASE_OWNER_URL:-$DATABASE_URL}"
  pg_url=$(echo "$url" | sed 's#postgresql+psycopg://#postgresql://#')
  i=0
  until pg_isready -d "$pg_url" -q; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then echo "PostgreSQL injoignable" >&2; exit 1; fi
    sleep 1
  done
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Application des migrations (rôle propriétaire)…"
  flask db upgrade
fi

exec "$@"
