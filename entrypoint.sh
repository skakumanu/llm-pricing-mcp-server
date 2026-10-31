#!/bin/sh
# Container entrypoint (v1.86.0).
#
# With LITESTREAM_REPLICA_URL set (a Fly secret), Litestream replicates the SQLite databases
# listed in /etc/litestream.yml continuously and supervises the app. Credentials are read by
# Litestream from LITESTREAM_ACCESS_KEY_ID / LITESTREAM_SECRET_ACCESS_KEY. With it unset
# (local runs, CI, a fresh deploy before the owner has a bucket) the app runs on its own.
set -eu

APP_CMD="python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 2"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
    # On an empty volume (lost or replaced), restore every replicated database that is missing
    # BEFORE the app starts. Otherwise the app would create empty databases and Litestream would
    # replicate those as the newest state. Existing databases are never overwritten, and a
    # database with no replica yet (first ever start) is simply skipped.
    for db in $(grep -E '^[[:space:]]*-[[:space:]]+path:' /etc/litestream.yml | awk '{print $3}'); do
        litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$db" \
            || { echo "entrypoint: restore of $db failed; refusing to start on an empty database, see docs/DEPLOYMENT.md"; exit 1; }
    done
    echo "entrypoint: replication on, starting litestream"
    # shellcheck disable=SC2086
    exec litestream replicate -config /etc/litestream.yml -exec "$APP_CMD"
fi

echo "entrypoint: LITESTREAM_REPLICA_URL not set, replication off"
# shellcheck disable=SC2086
exec $APP_CMD
