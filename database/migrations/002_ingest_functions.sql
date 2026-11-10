-- =============================================================================
-- 002_ingest_functions.sql
-- -----------------------------------------------------------------------------
-- WRITE interface used by the parsers. The Python side never writes to tables
-- directly; it only calls these three functions:
--
--   1. ingest.start_run(source_key, source_name, base_url)  -> run_id
--   2. ingest.upsert_event(run_id, event_jsonb)              -> 'inserted' | 'updated' | 'unchanged'
--      (called once per scraped event)
--   3. ingest.finish_run(run_id, status, item_errors, error_message, complete) -> run summary (jsonb)
--
-- All business rules about persistence live here, in one place:
--   * sources are registered automatically,
--   * events are de-duplicated by (source, external_id),
--   * real content changes are detected via a hash (no pointless rewrites),
--   * tags are normalised into a shared vocabulary,
--   * events that disappeared from the source are soft-deleted - but only after
--     a successful AND complete run, and only if they have not happened yet.
--
-- Functions are SECURITY DEFINER with a locked search_path, so the scraper's
-- database user needs nothing but EXECUTE on them (see 004_roles_and_grants.sql).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- ingest.start_run
-- Registers/updates the source row and opens a new scrape run.
-- Any run of the same source still marked 'running' is a leftover from a crashed
-- process; it is closed as 'failed' so monitoring stays truthful.
-- NOTE: the design assumes ONE scraper process per source at a time.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ingest.start_run(
    p_source_key  text,
    p_source_name text,
    p_base_url    text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_source_id integer;
    v_run_id    bigint;
BEGIN
    INSERT INTO core.sources AS s (key, name, base_url)
    VALUES (p_source_key, p_source_name, p_base_url)
    ON CONFLICT (key) DO UPDATE
        SET name       = EXCLUDED.name,
            base_url   = EXCLUDED.base_url,
            updated_at = CASE
                             WHEN (s.name, s.base_url) IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.base_url)
                             THEN now()
                             ELSE s.updated_at
                         END
    RETURNING s.id INTO v_source_id;

    UPDATE core.scrape_runs
       SET status        = 'failed',
           finished_at   = now(),
           error_message = coalesce(error_message, 'Abandoned: the process ended before finishing the run.')
     WHERE source_id = v_source_id
       AND status = 'running';

    INSERT INTO core.scrape_runs (source_id)
    VALUES (v_source_id)
    RETURNING id INTO v_run_id;

    RETURN v_run_id;
END;
$$;

COMMENT ON FUNCTION ingest.start_run(text, text, text) IS
    'Registers the source (if new) and opens a scrape run. Returns the run id.';


-- -----------------------------------------------------------------------------
-- ingest._sync_event_tags  (internal helper, not granted to anybody)
-- Makes the tag set of an event exactly equal to the given JSON array of labels.
-- Labels are slugified so that equal tags from different sources are shared.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ingest._sync_event_tags(p_event_id bigint, p_tags jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_slugs text[];
BEGIN
    -- Anything that is not a JSON array is treated as "no tags".
    IF p_tags IS NULL OR jsonb_typeof(p_tags) <> 'array' THEN
        p_tags := '[]'::jsonb;
    END IF;

    -- 1) Make sure every label exists in the shared vocabulary.
    --    The first label seen for a slug wins (keeps the original capitalisation).
    INSERT INTO core.tags (slug, label)
    SELECT DISTINCT ON (t.slug) t.slug, t.label
      FROM (
            SELECT core.slugify(x.value) AS slug, btrim(x.value) AS label, x.ordinality
              FROM jsonb_array_elements_text(p_tags) WITH ORDINALITY AS x(value, ordinality)
           ) AS t
     WHERE t.slug <> ''
     ORDER BY t.slug, t.ordinality
    ON CONFLICT (slug) DO NOTHING;

    -- 2) Desired set of slugs for this event.
    SELECT coalesce(array_agg(DISTINCT s.slug), '{}')
      INTO v_slugs
      FROM (SELECT core.slugify(value) AS slug FROM jsonb_array_elements_text(p_tags)) AS s
     WHERE s.slug <> '';

    -- 3) Remove assignments that are no longer present ...
    DELETE FROM core.event_tags AS et
     USING core.tags AS t
     WHERE et.event_id = p_event_id
       AND et.tag_id   = t.id
       AND NOT (t.slug = ANY (v_slugs));

    -- 4) ... and add the new ones.
    INSERT INTO core.event_tags (event_id, tag_id)
    SELECT p_event_id, t.id
      FROM core.tags AS t
     WHERE t.slug = ANY (v_slugs)
    ON CONFLICT DO NOTHING;
END;
$$;

COMMENT ON FUNCTION ingest._sync_event_tags(bigint, jsonb) IS
    'Internal: replaces the tags of an event with the given JSON array of labels.';


-- -----------------------------------------------------------------------------
-- ingest.upsert_event
-- Inserts or updates ONE event inside an open run.
--
-- Expected JSON payload (produced by Event.to_payload() in the parsers package):
-- {
--   "external_id": "calendarize-event-885",          -- required, unique per source
--   "title":       "Exhibition Thomas Mann",          -- required
--   "url":         "https://chn.tum.de/event/...",    -- required, original page
--   "starts_at":   "2026-11-06T11:30:00+01:00",       -- required, ISO-8601 with offset
--   "ends_at":     "2026-12-10T14:00:00+01:00",       -- optional
--   "all_day":     false,
--   "summary": "...", "description": "...", "image_url": "...",
--   "registration_url": "...", "location": "...", "organizer": "...",
--   "language": "en", "tags": ["General Public"], "extra": {"ics_url": "..."}
-- }
--
-- Returns what happened so the caller can log/count it.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ingest.upsert_event(p_run_id bigint, p_event jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_run          core.scrape_runs%ROWTYPE;
    v_external_id  text;
    v_hash         text;
    v_event_id     bigint;
    v_old_hash     text;
    v_outcome      text;
BEGIN
    -- ---- Validate the run -------------------------------------------------
    SELECT * INTO v_run FROM core.scrape_runs WHERE id = p_run_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Scrape run % does not exist', p_run_id
              USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_run.status <> 'running' THEN
        RAISE EXCEPTION 'Scrape run % is already finished (status=%)', p_run_id, v_run.status
              USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ---- Validate the payload ----------------------------------------------
    -- title/url/starts_at are enforced by the table's NOT NULL/CHECK constraints;
    -- external_id is checked here because it is needed for the lookup.
    IF p_event IS NULL OR jsonb_typeof(p_event) <> 'object' THEN
        RAISE EXCEPTION 'Event payload must be a JSON object'
              USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_external_id := nullif(btrim(p_event ->> 'external_id'), '');
    IF v_external_id IS NULL THEN
        RAISE EXCEPTION 'Event payload is missing "external_id"'
              USING ERRCODE = 'not_null_violation';
    END IF;

    -- jsonb has a canonical text form (sorted keys, no whitespace variance),
    -- so equal payloads always produce equal hashes.
    v_hash := md5(p_event::text);

    -- ---- Insert / update / touch ----------------------------------------------
    SELECT e.id, e.content_hash
      INTO v_event_id, v_old_hash
      FROM core.events AS e
     WHERE e.source_id = v_run.source_id
       AND e.external_id = v_external_id
       FOR UPDATE;

    IF v_event_id IS NULL THEN
        INSERT INTO core.events (
            source_id, external_id, title, summary, description, url,
            registration_url, image_url, starts_at, ends_at, all_day,
            location, organizer, language, extra, content_hash, last_seen_run_id
        )
        VALUES (
            v_run.source_id,
            v_external_id,
            nullif(btrim(p_event ->> 'title'), ''),
            nullif(btrim(p_event ->> 'summary'), ''),
            nullif(btrim(p_event ->> 'description'), ''),
            nullif(btrim(p_event ->> 'url'), ''),
            nullif(btrim(p_event ->> 'registration_url'), ''),
            nullif(btrim(p_event ->> 'image_url'), ''),
            (p_event ->> 'starts_at')::timestamptz,
            (p_event ->> 'ends_at')::timestamptz,
            coalesce((p_event ->> 'all_day')::boolean, false),
            nullif(btrim(p_event ->> 'location'), ''),
            nullif(btrim(p_event ->> 'organizer'), ''),
            nullif(btrim(p_event ->> 'language'), ''),
            CASE WHEN jsonb_typeof(p_event -> 'extra') = 'object' THEN p_event -> 'extra' ELSE '{}'::jsonb END,
            v_hash,
            p_run_id
        )
        RETURNING id INTO v_event_id;
        v_outcome := 'inserted';

    ELSIF v_old_hash IS DISTINCT FROM v_hash THEN
        UPDATE core.events
           SET title            = nullif(btrim(p_event ->> 'title'), ''),
               summary          = nullif(btrim(p_event ->> 'summary'), ''),
               description      = nullif(btrim(p_event ->> 'description'), ''),
               url              = nullif(btrim(p_event ->> 'url'), ''),
               registration_url = nullif(btrim(p_event ->> 'registration_url'), ''),
               image_url        = nullif(btrim(p_event ->> 'image_url'), ''),
               starts_at        = (p_event ->> 'starts_at')::timestamptz,
               ends_at          = (p_event ->> 'ends_at')::timestamptz,
               all_day          = coalesce((p_event ->> 'all_day')::boolean, false),
               location         = nullif(btrim(p_event ->> 'location'), ''),
               organizer        = nullif(btrim(p_event ->> 'organizer'), ''),
               language         = nullif(btrim(p_event ->> 'language'), ''),
               extra            = CASE WHEN jsonb_typeof(p_event -> 'extra') = 'object'
                                       THEN p_event -> 'extra' ELSE '{}'::jsonb END,
               content_hash     = v_hash,
               is_active        = true,
               last_seen_run_id = p_run_id,
               last_seen_at     = now(),
               updated_at       = now()
         WHERE id = v_event_id;
        v_outcome := 'updated';

    ELSE
        -- Same content as last time: only record that we saw it (and revive it
        -- if it had been soft-deleted earlier).
        UPDATE core.events
           SET is_active        = true,
               last_seen_run_id = p_run_id,
               last_seen_at     = now()
         WHERE id = v_event_id;
        v_outcome := 'unchanged';
    END IF;

    IF v_outcome <> 'unchanged' THEN
        PERFORM ingest._sync_event_tags(v_event_id, p_event -> 'tags');
    END IF;

    -- ---- Per-run statistics -----------------------------------------------------
    UPDATE core.scrape_runs
       SET events_seen = events_seen + 1,
           inserted    = inserted  + (v_outcome = 'inserted')::int,
           updated     = updated   + (v_outcome = 'updated')::int,
           unchanged   = unchanged + (v_outcome = 'unchanged')::int
     WHERE id = p_run_id;

    RETURN v_outcome;
END;
$$;

COMMENT ON FUNCTION ingest.upsert_event(bigint, jsonb) IS
    'Idempotently stores one scraped event. Returns inserted | updated | unchanged.';


-- -----------------------------------------------------------------------------
-- ingest.finish_run
-- Closes a run and, when it is safe, soft-deletes events that vanished.
--
-- Soft delete happens ONLY IF all of these hold:
--   * p_status = 'succeeded'      (no fatal error, no per-item errors)
--   * p_complete = true           (the parser saw the source's full listing)
--   * at least one event was seen (protects against a silently broken parser
--                                  wiping out the whole source)
-- and it only affects events that have not ended yet: past events naturally
-- drop off "upcoming" listings and must stay in the archive.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ingest.finish_run(
    p_run_id        bigint,
    p_status        text,
    p_item_errors   integer DEFAULT 0,
    p_error_message text    DEFAULT NULL,
    p_complete      boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_run          core.scrape_runs%ROWTYPE;
    v_deactivated  integer := 0;
BEGIN
    IF p_status NOT IN ('succeeded', 'partial', 'failed') THEN
        RAISE EXCEPTION 'Invalid final status "%" (expected succeeded | partial | failed)', p_status
              USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_run FROM core.scrape_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Scrape run % does not exist', p_run_id
              USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_run.status <> 'running' THEN
        RAISE EXCEPTION 'Scrape run % is already finished (status=%)', p_run_id, v_run.status
              USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_status = 'succeeded' AND p_complete AND v_run.events_seen > 0 THEN
        UPDATE core.events
           SET is_active  = false,
               updated_at = now()
         WHERE source_id = v_run.source_id
           AND is_active
           AND last_seen_run_id IS DISTINCT FROM p_run_id
           AND coalesce(ends_at, starts_at) >= v_run.started_at;
        GET DIAGNOSTICS v_deactivated = ROW_COUNT;
    END IF;

    UPDATE core.scrape_runs
       SET status        = p_status,
           finished_at   = now(),
           item_errors   = coalesce(p_item_errors, 0),
           error_message = p_error_message,
           deactivated   = v_deactivated
     WHERE id = p_run_id
    RETURNING * INTO v_run;

    RETURN to_jsonb(v_run);
END;
$$;

COMMENT ON FUNCTION ingest.finish_run(bigint, text, integer, text, boolean) IS
    'Closes a scrape run; soft-deletes vanished upcoming events after a complete, successful run.';

