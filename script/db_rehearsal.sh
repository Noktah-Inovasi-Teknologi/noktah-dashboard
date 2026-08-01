#!/bin/bash
# Clone the live noktah_dashboard database into a throwaway database so
# migrations can be rehearsed against real data before touching production.
# See specs/004-relational-spine/quickstart.md step 1.
#
# Usage:
#   script/db_rehearsal.sh create      # (re)create spine_rehearsal from a fresh dump
#   script/db_rehearsal.sh drop        # drop spine_rehearsal
set -euo pipefail

CONTAINER="${DB_REHEARSAL_CONTAINER:-postgres}"
USER="${DB_REHEARSAL_USER:-noktah}"
SOURCE_DB="${DB_REHEARSAL_SOURCE_DB:-noktah_dashboard}"
TARGET_DB="${DB_REHEARSAL_TARGET_DB:-spine_rehearsal}"

action="${1:-create}"

case "$action" in
  create)
    echo "Dropping any existing '$TARGET_DB' ..."
    docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$TARGET_DB"
    echo "Creating '$TARGET_DB' ..."
    docker exec "$CONTAINER" createdb -U "$USER" "$TARGET_DB"
    echo "Cloning '$SOURCE_DB' -> '$TARGET_DB' ..."
    docker exec "$CONTAINER" pg_dump -U "$USER" "$SOURCE_DB" \
      | docker exec -i "$CONTAINER" psql -U "$USER" -d "$TARGET_DB" -v ON_ERROR_STOP=1 >/dev/null
    echo "Ready: $TARGET_DB"
    ;;
  drop)
    docker exec "$CONTAINER" dropdb -U "$USER" --if-exists "$TARGET_DB"
    echo "Dropped: $TARGET_DB"
    ;;
  *)
    echo "Usage: $0 {create|drop}" >&2
    exit 1
    ;;
esac
