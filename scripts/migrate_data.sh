#!/usr/bin/env bash
#
# One-time data migration: copy existing curator tables from a source Postgres DB
# into the standalone curator database. READ-ONLY on the source (pg_dump only).
#
# Run this AFTER `docker compose up -d db` has created the fresh schema.
#
#   SRC_DSN=postgresql://user:pass@host:port/dbname ./scripts/migrate_data.sh
#
# Requires pg_dump and psql on PATH, or adapt to run via docker exec.
set -euo pipefail

# Source DB — set SRC_DSN to your existing database connection string.
: "${SRC_DSN:?Set SRC_DSN=postgresql://user:pass@host:port/dbname before running}"

# Destination = the standalone curator DB (docker-compose port 5434).
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (same value as your .env) before running}"
DST_DSN="${DST_DSN:-postgresql://curator_user:${POSTGRES_PASSWORD}@localhost:5434/curator}"

# FK-safe order: parents (registry, discovery_runs) before children (evals), then proposals/alerts.
TABLES=(llm_registry llm_discovery_runs llm_evals llm_proposals llm_alerts)

echo "→ Exporting ${#TABLES[@]} tables from source DB (read-only)…"
DUMP=/tmp/curator_data_$(date +%s).sql
pg_dump "$SRC_DSN" --data-only --no-owner --no-privileges \
  "${TABLES[@]/#/--table=}" > "$DUMP"
echo "  wrote $DUMP ($(wc -l < "$DUMP") lines)"

echo "→ Loading into standalone curator DB…"
psql "$DST_DSN" -v ON_ERROR_STOP=1 -f "$DUMP"

echo "→ Row counts in curator DB:"
for t in "${TABLES[@]}"; do
  printf '   %-22s %s\n' "$t" "$(psql "$DST_DSN" -tAc "SELECT count(*) FROM $t")"
done
echo "✓ Migration complete. Source database was not modified."
