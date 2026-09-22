#!/bin/sh
# Sauvegarde PostgreSQL quotidienne au format custom, avec rétention (BACKUP_RETENTION_DAYS, 30 par défaut).
# Utilisé par le service « backup » de docker-compose.prod.yml ; peut aussi être lancé depuis le cron de l'hôte.
set -eu
: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}"
dir="${BACKUP_DIR:-/backups}"
mkdir -p "$dir"
while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  file="$dir/labqualite-$stamp.dump"
  if pg_dump --format=custom --no-owner --file="$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "Sauvegarde créée : $file"
  else
    rm -f "$file.tmp"
    echo "ÉCHEC de la sauvegarde $stamp" >&2
  fi
  find "$dir" -name 'labqualite-*.dump' -mtime +"${BACKUP_RETENTION_DAYS:-30}" -delete
  [ "${BACKUP_ONCE:-0}" = "1" ] && exit 0
  sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
