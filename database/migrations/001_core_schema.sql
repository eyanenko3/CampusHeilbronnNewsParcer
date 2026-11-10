-- =============================================================================
-- 001_core_schema.sql
-- -----------------------------------------------------------------------------
-- Core relational model for the Campus Heilbronn event aggregator.
--
-- Three schemas separate responsibilities:
--
--   core    -> the actual tables. Nobody except the owner touches them directly.
--   ingest  -> WRITE interface used by the parsers (see 002_ingest_functions.sql)
--   api     -> READ interface used by the REST API (see 003_api_read_layer.sql)
--
-- Keeping the parsers and the API behind function/view "contracts" means the
-- tables can evolve (new columns, refactors, partitions) without breaking either
-- side, and each side gets only the privileges it needs (see scripts/grants.sql).
--
-- No PostgreSQL extensions are required, so this runs on any managed Postgres
-- (Cloud SQL, Neon, Supabase, RDS, plain Docker image, ...).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS api;

COMMENT ON SCHEMA core   IS 'Tables of the event aggregator. Access only through the ingest/api schemas.';
COMMENT ON SCHEMA ingest IS 'Write interface for the scraper/parsers (SECURITY DEFINER functions).';
COMMENT ON SCHEMA api    IS 'Read-only interface for the public REST API (views + functions).';


-- -----------------------------------------------------------------------------
-- Helper: slugify a free-text label into a stable, URL-safe identifier.
--   'Current Students'  -> 'current-students'
--   'Führung & Tour'     -> 'fuehrung-tour'
-- Used for tags so that "Campus", "campus " and "CAMPUS" collapse into one tag
-- and so that filters in URLs (?tag=current-students) are predictable.
-- IMMUTABLE so it can be used in indexes / generated columns if ever needed.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.slugify(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT btrim(
        regexp_replace(
            replace(replace(replace(replace(lower(btrim(p_text)),
                'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss'),
            '[^a-z0-9]+', '-', 'g'
        ),
        '-'
    );
$$;

COMMENT ON FUNCTION core.slugify(text) IS
    'Normalises a label into a lowercase, dash-separated slug (German umlauts transliterated).';


-- -----------------------------------------------------------------------------
-- core.sources
-- One row per parser (= one website / feed). Rows are created automatically the
-- first time a parser runs (ingest.start_run), so adding a parser in Python
-- never requires a manual SQL change.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.sources (
    id          integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key         text        NOT NULL UNIQUE
                            CHECK (key ~ '^[a-z0-9_]+$'),
    name        text        NOT NULL CHECK (length(btrim(name)) > 0),
    base_url    text        NOT NULL CHECK (base_url ~* '^https?://'),
    -- Lets an operator hide a whole source from the app without deleting data.
    is_enabled  boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  core.sources     IS 'A website/feed that is scraped by exactly one parser.';
COMMENT ON COLUMN core.sources.key IS 'Stable identifier, equals BaseParser.key in the Python code.';


-- -----------------------------------------------------------------------------
-- core.scrape_runs
-- Audit log: every execution of a parser. Used for monitoring ("when did the
-- CHN parser last succeed?") and for safe removal detection (an event that is
-- missing from a *complete* run has been taken down by the source).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.scrape_runs (
    id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      integer     NOT NULL REFERENCES core.sources (id) ON DELETE CASCADE,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running'
                               CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    events_seen    integer     NOT NULL DEFAULT 0,
    inserted       integer     NOT NULL DEFAULT 0,
    updated        integer     NOT NULL DEFAULT 0,
    unchanged      integer     NOT NULL DEFAULT 0,
    deactivated    integer     NOT NULL DEFAULT 0,
    item_errors    integer     NOT NULL DEFAULT 0,
    error_message  text,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX IF NOT EXISTS scrape_runs_source_started_idx
    ON core.scrape_runs (source_id, started_at DESC);

COMMENT ON TABLE core.scrape_runs IS
    'One row per parser execution. status: running | succeeded | partial (some items failed) | failed.';


-- -----------------------------------------------------------------------------
-- core.events
-- The central entity. Natural key = (source_id, external_id): the id the source
-- itself uses for the event, which makes re-scraping idempotent (upsert).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.events (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id         integer     NOT NULL REFERENCES core.sources (id) ON DELETE CASCADE,
    external_id       text        NOT NULL CHECK (length(btrim(external_id)) > 0),

    -- What the student sees ------------------------------------------------
    title             text        NOT NULL CHECK (length(btrim(title)) > 0),
    summary           text,                       -- short teaser for list cards
    description       text,                       -- full plain-text description
    url               text        NOT NULL CHECK (url ~* '^https?://'),  -- original page
    registration_url  text        CHECK (registration_url ~* '^https?://'),
    image_url         text        CHECK (image_url ~* '^https?://'),

    -- When / where ----------------------------------------------------------
    starts_at         timestamptz NOT NULL,
    ends_at           timestamptz,
    all_day           boolean     NOT NULL DEFAULT false,  -- true = no time known
    location          text,
    organizer         text,
    language          text,                       -- ISO 639-1, e.g. 'en', 'de'

    -- Parser specific leftovers (e.g. ics_url) without schema changes.
    extra             jsonb       NOT NULL DEFAULT '{}'::jsonb
                                  CHECK (jsonb_typeof(extra) = 'object'),

    -- Bookkeeping -----------------------------------------------------------
    content_hash      text        NOT NULL,       -- md5 of the ingested payload
    is_active         boolean     NOT NULL DEFAULT true,  -- false = removed at source
    last_seen_run_id  bigint      REFERENCES core.scrape_runs (id) ON DELETE SET NULL,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),  -- last *content* change

    -- Full-text search over the visible fields. 'simple' config = no stemming,
    -- which behaves predictably for mixed English/German content.
    search_vector     tsvector GENERATED ALWAYS AS (
                          setweight(to_tsvector('simple', coalesce(title, '')),       'A') ||
                          setweight(to_tsvector('simple', coalesce(summary, '')),     'B') ||
                          setweight(to_tsvector('simple', coalesce(organizer, '')),   'B') ||
                          setweight(to_tsvector('simple', coalesce(description, '')), 'C') ||
                          setweight(to_tsvector('simple', coalesce(location, '')),    'D')
                      ) STORED,

    CONSTRAINT events_source_external_uk UNIQUE (source_id, external_id),
    CONSTRAINT events_time_order_ck      CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

-- The app's main query: "active events that have not ended yet, by start time".
CREATE INDEX IF NOT EXISTS events_active_starts_idx
    ON core.events (starts_at) WHERE is_active;
CREATE INDEX IF NOT EXISTS events_active_ends_idx
    ON core.events ((coalesce(ends_at, starts_at))) WHERE is_active;
CREATE INDEX IF NOT EXISTS events_search_idx
    ON core.events USING gin (search_vector);
CREATE INDEX IF NOT EXISTS events_last_seen_run_idx
    ON core.events (source_id, last_seen_run_id);

COMMENT ON TABLE  core.events              IS 'Normalised event scraped from a source.';
COMMENT ON COLUMN core.events.external_id  IS 'Id of the event at the source; unique per source.';
COMMENT ON COLUMN core.events.all_day      IS 'True when the source gave a date but no time of day.';
COMMENT ON COLUMN core.events.content_hash IS 'Hash of the last ingested payload; used to detect real changes.';
COMMENT ON COLUMN core.events.is_active    IS 'False once a complete scrape run no longer finds the (not yet finished) event.';


-- -----------------------------------------------------------------------------
-- Tags (many-to-many). Sources expose audiences/categories such as
-- "Current Students", "Campus", "Company"; they become filter chips in the app.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.tags (
    id          integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug        text        NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    label       text        NOT NULL CHECK (length(btrim(label)) > 0),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.event_tags (
    event_id  bigint  NOT NULL REFERENCES core.events (id) ON DELETE CASCADE,
    tag_id    integer NOT NULL REFERENCES core.tags (id)   ON DELETE CASCADE,
    PRIMARY KEY (event_id, tag_id)
);

-- PK covers lookups by event; this one covers "all events with tag X".
CREATE INDEX IF NOT EXISTS event_tags_tag_idx ON core.event_tags (tag_id, event_id);

COMMENT ON TABLE core.tags       IS 'Normalised tag/category vocabulary shared across all sources.';
COMMENT ON TABLE core.event_tags IS 'Assignment of tags to events.';

