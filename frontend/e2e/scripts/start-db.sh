#!/usr/bin/env bash
# Native (no Docker) Postgres 16 + Redis for the browser tests, on a RAM disk.
# For machines where Docker is not available (the cloud sandbox). On your own
# PC use `docker compose up -d db redis` from backend/ instead (RUNBOOK §3.2).
#
#   frontend/e2e/scripts/start-db.sh     # creates the cluster on first run
#
# Needs: postgresql-16 with pgvector >= 0.8 installed, redis-server, root (it
# runs Postgres as the `postgres` user). Database: flowpilot_e2e on port 5433,
# trust auth on 127.0.0.1 only. All data is lost when the machine restarts.
set -euo pipefail

PGDATA="${E2E_PGDATA:-/dev/shm/pgdata}"
PGPORT="${E2E_PGPORT:-5433}"
PGBIN="${E2E_PGBIN:-/usr/lib/postgresql/16/bin}"
DB="${E2E_DB:-flowpilot_e2e}"

if ! redis-cli ping >/dev/null 2>&1; then
  setsid nohup redis-server --port 6379 --save '' --appendonly no >/tmp/flowpilot-e2e-redis.log 2>&1 &
fi

if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  su postgres -c "$PGBIN/initdb -D $PGDATA -U postgres -A trust" >/tmp/flowpilot-e2e-initdb.log
fi

if ! pg_isready -h localhost -p "$PGPORT" >/dev/null 2>&1; then
  su postgres -c "setsid nohup $PGBIN/postgres -D $PGDATA -p $PGPORT -c fsync=off \
    -c synchronous_commit=off -c full_page_writes=off -c max_connections=200 \
    -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp >/tmp/flowpilot-e2e-pg.log 2>&1 &"
  for _ in $(seq 1 30); do pg_isready -h localhost -p "$PGPORT" >/dev/null 2>&1 && break; sleep 1; done
fi

psql -h localhost -p "$PGPORT" -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1 ||
  psql -h localhost -p "$PGPORT" -U postgres -c "CREATE DATABASE $DB" >/dev/null
redis-cli ping >/dev/null && pg_isready -h localhost -p "$PGPORT"
