#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services
#
# Nightly PostgreSQL backup with local rotation (off-site copy: Hetzner Storage Box, later).
#
#   scripts/backup.sh                       # uses the environment variables below
#   crontab: 15 3 * * * /opt/planificateur-gardes/scripts/backup.sh >> /var/log/gardes-backup.log 2>&1
#
# Environment:
#   POSTGRES_CONTAINER  run pg_dump inside this Docker container (shared PostgreSQL); if empty,
#                       a local pg_dump is used with the usual PG* variables / ~/.pgpass
#   PGDATABASE          database name          (default: planificateur_gardes)
#   PGUSER              database user          (default: planificateur_gardes)
#   BACKUP_DIR          destination directory  (default: /var/backups/planificateur-gardes)
#   RETENTION_DAYS      days of dumps to keep  (default: 14)
#
# Dumps use the custom format (pg_restore-able, compressed) and are only readable by the
# user running the script: they contain personal data (names, emails, schedules).
set -euo pipefail

PGDATABASE="${PGDATABASE:-planificateur_gardes}"
PGUSER="${PGUSER:-planificateur_gardes}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/planificateur-gardes}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-}"

umask 077
mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/${PGDATABASE}-${timestamp}.dump"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

if [[ -n "$POSTGRES_CONTAINER" ]]; then
    docker exec "$POSTGRES_CONTAINER" pg_dump --format=custom --no-owner \
        --username="$PGUSER" "$PGDATABASE" > "$partial"
else
    pg_dump --format=custom --no-owner --username="$PGUSER" "$PGDATABASE" > "$partial"
fi

# A truncated or empty dump must not replace good ones.
if [[ ! -s "$partial" ]]; then
    echo "$(date -u +%FT%TZ) ERROR: empty dump, keeping previous backups" >&2
    exit 1
fi
mv "$partial" "$target"
trap - EXIT

find "$BACKUP_DIR" -maxdepth 1 -type f -name "${PGDATABASE}-*.dump" \
    -mtime +"$RETENTION_DAYS" -print -delete

echo "$(date -u +%FT%TZ) OK: $target ($(du -h "$target" | cut -f1))"
