-- =============================================================================
-- create_app_users.sql
-- -----------------------------------------------------------------------------
-- Creates (or updates the password of) the two LOGIN users the services use and
-- puts them into the least-privilege group roles from 004_roles_and_grants.sql.
-- Idempotent: safe to run on every deployment.
--
-- Invoked by migrate.sh, or manually:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v scraper_user=chn_scraper -v scraper_password=... \
--        -v api_user=chn_api         -v api_password=...     \
--        -f database/scripts/create_app_users.sql
--
-- \gexec executes the generated statement; format(%I / %L) quotes identifiers
-- and literals safely, so passwords with special characters are fine.
-- =============================================================================

SELECT format('CREATE ROLE %I LOGIN', :'scraper_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'scraper_user')
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'scraper_user', :'scraper_password')
\gexec
GRANT chn_ingest TO :"scraper_user";

SELECT format('CREATE ROLE %I LOGIN', :'api_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'api_user')
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'api_user', :'api_password')
\gexec
GRANT chn_reader TO :"api_user";

