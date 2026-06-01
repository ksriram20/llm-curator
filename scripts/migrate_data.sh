#!/usr/bin/env bash
#
# Stage 1 -> 2 bridge: copy existing curator data out of PARCON's parcon_csr
# into the detached curator database. READ-ONLY on PARCON (pg_dump only) — it
# never writes to or alters parcon_csr. Run this AFTER `docker compose up -d db`
# has created the fresh schema, and AFTER you've reviewed it.
#
#   ./scripts/migrate_data.sh
#
# Requires: pg_dump/psql on PATH (or run the psql side via the db container).
set -euo pipefail

# Source = PARCON's shared DB (read-only).
# PARCON's Postgres lives in the 'pgvector' container (host port 5433 → container 5432).
# Run via: docker exec pgvector pg_dump ... | psql ...  (see inline below)
SRC_DSN="${SRC_DSN:-postgresql://parcon:parcon2026@localhost:5432/parcon_csr}"  # pragma: allowlist secret
# NOTE: pg_dump must run inside the pgvector container — pg_dump is not on the host PATH.
# Use: docker exec pgvector pg_dump "$SRC_DSN" ... | docker compose exec -T db psql ...

# Destination = the detached curator DB, exposed by docker-compose on 5434.
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (same value as your .env) before running}"
DST_DSN="${DST_DSN:-postgresql://parcon:${POSTGRES_PASSWORD}@localhost:5434/llm_curator}"

# FK-safe order: parents (registry, discovery_runs) before children (evals), then proposals/alerts.
TABLES=(llm_registry llm_discovery_runs llm_evals llm_proposals llm_alerts)

echo "→ Exporting ${#TABLES[@]} tables from parcon_csr (read-only)…"
DUMP=/tmp/curator_data_$(date +%s).sql
pg_dump "$SRC_DSN" --data-only --no-owner --no-privileges \
  "${TABLES[@]/#/--table=}" > "$DUMP"
echo "  wrote $DUMP ($(wc -l < "$DUMP") lines)"

echo "→ Loading into detached curator DB…"
psql "$DST_DSN" -v ON_ERROR_STOP=1 -f "$DUMP"

echo "→ Row counts in detached DB:"
for t in "${TABLES[@]}"; do
  printf '   %-22s %s\n' "$t" "$(psql "$DST_DSN" -tAc "SELECT count(*) FROM $t")"
done
echo "✓ Data migration complete. PARCON's parcon_csr was not modified."
