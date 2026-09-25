#!/bin/bash
# Prove config/postgres/init.sql (the greenfield path) and the numbered
# migration chain (the upgrade path) produce structurally identical databases
# (FR-029 / SC-006). Builds two throwaway databases and diffs tables, columns,
# indexes, and constraints. Exits non-zero on any difference.
#
# See specs/004-relational-spine/contracts/migrations.md.
set -euo pipefail

CONTAINER="${DB_REHEARSAL_CONTAINER:-postgres}"
USER="${DB_REHEARSAL_USER:-noktah}"
DB_A="parity_init_sql"
DB_B="parity_migration_chain"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="$ROOT_DIR/config/postgres/migrations"
INIT_SQL="$ROOT_DIR/config/postgres/init.sql"

echo "== Building '$DB_A' from init.sql ..."
docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$DB_A"
docker exec "$CONTAINER" createdb -U "$USER" "$DB_A"
# init.sql's preamble (CREATE DATABASE / GRANT) targets the maintenance
# connection at container first-boot; only the table-defining portion
# (from the extensions onward) applies to an already-created database.
tail -n +18 "$INIT_SQL" | docker exec -i "$CONTAINER" psql -U "$USER" -d "$DB_A" -v ON_ERROR_STOP=1 >/dev/null

echo "== Building '$DB_B' from the migration chain ..."
docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$DB_B"
docker exec "$CONTAINER" createdb -U "$USER" "$DB_B"
docker exec "$CONTAINER" psql -U "$USER" -d "$DB_B" -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null
# Explicit, ordered list — never a glob. `00N_*.sql` also matches
# `00N_*.down.sql`, and applying a migration followed by its own reversal
# silently undoes it (schema.md).
for m in 000_schema_migrations 001_knowledge_records 002_harvested_items \
         003_harvested_signals 004_relational_spine 005_link_existing \
         006_enforce_account_link 007_signal_field_coverage \
         008_observation_history 009_structured_extraction \
         010_hub_registry_card 011_client_brand_required \
         012_hub_units_roles_permissions; do
  docker exec -i "$CONTAINER" psql -U "$USER" -d "$DB_B" -v ON_ERROR_STOP=1 < "$MIGRATIONS_DIR/$m.sql" >/dev/null
done

echo "== Diffing structure ..."
diff_found=0

compare() {
  local label="$1" query="$2"
  local a b
  a=$(docker exec "$CONTAINER" psql -U "$USER" -d "$DB_A" -At -c "$query")
  b=$(docker exec "$CONTAINER" psql -U "$USER" -d "$DB_B" -At -c "$query")
  if [ "$a" != "$b" ]; then
    echo "DIFF ($label):"
    diff <(echo "$a") <(echo "$b") || true
    diff_found=1
  fi
}

compare "tables" \
  "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1"

compare "columns" \
  "SELECT table_name || '.' || column_name || ':' || data_type || ':' || is_nullable || ':' || COALESCE(column_default,'')
   FROM information_schema.columns WHERE table_schema='public' ORDER BY 1"

compare "indexes" \
  "SELECT indexname || ' -- ' || regexp_replace(indexdef, 'parity_init_sql|parity_migration_chain', 'DB', 'g')
   FROM pg_indexes WHERE schemaname='public' ORDER BY 1"

compare "constraints" \
  "SELECT conname || ' -- ' || pg_get_constraintdef(oid)
   FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1"

# Views are already caught by the 'tables' compare above (information_schema.tables
# lists them), but only by NAME. A view whose CASE branches drift between the two
# paths would pass that check while classifying rows differently — which for
# velocity_status (feature 006) is the difference between "exactly one reason per
# item" holding and not.
compare "view definitions" \
  "SELECT viewname || ' -- ' || pg_get_viewdef(('public.' || viewname)::regclass, true)
   FROM pg_views WHERE schemaname='public' ORDER BY 1"

docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$DB_A"
docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$DB_B"

if [ "$diff_found" -ne 0 ]; then
  echo "SCHEMA PARITY FAILED — init.sql and the migration chain disagree (see diffs above)."
  exit 1
fi

echo "SCHEMA PARITY OK — init.sql and the migration chain are structurally identical."
