#!/bin/sh
# =============================================================================
# migrate.sh - apply database/migrations/*.sql exactly once, in order.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db ./database/migrate.sh
#
# How it works:
#   * public.schema_migrations remembers which files were applied.
#   * Each pending file runs in its own transaction together with the INSERT
#     that records it, so a failing migration leaves no trace (all-or-nothing).
#   * Files are applied in lexical order -> always prefix them with a number
#     (005_add_xyz.sql). NEVER edit a file that was already applied somewhere;
#     add a new one instead.
#
# Optional: if SCRAPER_DB_PASSWORD and API_DB_PASSWORD are set, least-privilege
# login users are created/updated afterwards (scripts/create_app_users.sql).
#
# POSIX sh on purpose: runs inside the stock postgres:*-alpine image.
# =============================================================================
set -eu

: "${DATABASE_URL:?DATABASE_URL must be set, e.g. postgresql://user:pass@localhost:5432/chn}"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
MIGRATIONS_DIR="$SCRIPT_DIR/migrations"
PSQL="psql $DATABASE_URL -v ON_ERROR_STOP=1 --quiet --no-psqlrc"

echo "[migrate] ensuring bookkeeping table"
$PSQL -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (
              version    text        PRIMARY KEY,
              applied_at timestamptz NOT NULL DEFAULT now()
          );"

applied_count=0
for file in "$MIGRATIONS_DIR"/*.sql; do
    [ -e "$file" ] || continue          # empty directory guard
    version=$(basename "$file")

    already=$($PSQL --tuples-only --no-align \
        -c "SELECT 1 FROM public.schema_migrations WHERE version = '$version'")
    if [ "$already" = "1" ]; then
        echo "[migrate] skip   $version (already applied)"
        continue
    fi

    echo "[migrate] apply  $version"
    $PSQL --single-transaction \
        -f "$file" \
        -c "INSERT INTO public.schema_migrations (version) VALUES ('$version')"
    applied_count=$((applied_count + 1))
done
echo "[migrate] done - $applied_count new migration(s) applied"

if [ -n "${SCRAPER_DB_PASSWORD:-}" ] && [ -n "${API_DB_PASSWORD:-}" ]; then
    echo "[migrate] creating/updating application login users"
    $PSQL \
        -v scraper_user="${SCRAPER_DB_USER:-chn_scraper}" \
        -v scraper_password="$SCRAPER_DB_PASSWORD" \
        -v api_user="${API_DB_USER:-chn_api}" \
        -v api_password="$API_DB_PASSWORD" \
        -f "$SCRIPT_DIR/scripts/create_app_users.sql"
fi

