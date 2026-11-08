#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ] || [ -z "${BACKUP_DIR:-}" ]; then
    echo "Нужны DATABASE_URL и BACKUP_DIR" >&2
    exit 2
fi

mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump "$DATABASE_URL" -Fc -f "$BACKUP_DIR/agzs-$stamp.dump"
echo "$BACKUP_DIR/agzs-$stamp.dump"
