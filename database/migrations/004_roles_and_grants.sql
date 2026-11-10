-- =============================================================================
-- 004_roles_and_grants.sql
-- -----------------------------------------------------------------------------
-- Principle of least privilege. Two NOLOGIN "group" roles:
--
--   chn_ingest  -> may only EXECUTE the public ingest.* functions (the scraper)
--   chn_reader  -> may only SELECT api.* views / EXECUTE api.* functions (the API)
--
-- Neither role can touch core.* directly. Actual LOGIN users are created per
-- environment by database/scripts/create_app_users.sql and are made members of
-- these roles. Locally you may also simply use the owner account everywhere.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chn_ingest') THEN
        CREATE ROLE chn_ingest NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chn_reader') THEN
        CREATE ROLE chn_reader NOLOGIN;
    END IF;
END;
$$;

-- Lock everything down first ---------------------------------------------------
REVOKE ALL ON SCHEMA core, ingest, api FROM PUBLIC;
REVOKE ALL ON ALL TABLES    IN SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core, ingest, api FROM PUBLIC;

-- Scraper ----------------------------------------------------------------------
GRANT USAGE   ON SCHEMA ingest TO chn_ingest;
GRANT EXECUTE ON FUNCTION ingest.start_run(text, text, text)                    TO chn_ingest;
GRANT EXECUTE ON FUNCTION ingest.upsert_event(bigint, jsonb)                    TO chn_ingest;
GRANT EXECUTE ON FUNCTION ingest.finish_run(bigint, text, integer, text, boolean) TO chn_ingest;
-- ingest._sync_event_tags is intentionally NOT granted (internal helper).

-- API --------------------------------------------------------------------------
GRANT USAGE   ON SCHEMA api TO chn_reader;
GRANT SELECT  ON ALL TABLES    IN SCHEMA api TO chn_reader;   -- views count as tables here
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA api TO chn_reader;

-- Future api.* objects created by the migration owner are readable automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA api GRANT SELECT  ON TABLES    TO chn_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA api GRANT EXECUTE ON FUNCTIONS TO chn_reader;
-- New ingest.* functions must be granted explicitly in their migration (deliberate).
ALTER DEFAULT PRIVILEGES IN SCHEMA ingest REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

