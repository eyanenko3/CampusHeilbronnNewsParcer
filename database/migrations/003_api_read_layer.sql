-- =============================================================================
-- 003_api_read_layer.sql
-- -----------------------------------------------------------------------------
-- READ interface used by the REST API (and therefore by the React Native app).
--
--   api.events          view      every visible event, tags pre-aggregated
--   api.tags            view      tag vocabulary + number of upcoming events
--   api.sources         view      sources + health of their latest run
--   api.filter_events() function  all filters in ONE place (no pagination)
--   api.search_events() function  filter + order + paginate
--   api.count_events()  function  filter + count (for "x results" / paging)
--
-- The REST layer is intentionally thin: it maps query parameters 1:1 onto the
-- function arguments. A new filter is therefore added here, in SQL, first.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- api.events
-- "Visible" = the event is active and its source is enabled.
-- tags      = JSON array [{slug, label}, ...] ready to be rendered as chips.
-- tag_slugs = text[] used for fast filtering with the && (overlap) operator.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW api.events AS
SELECT
    e.id,
    s.key                                   AS source_key,
    s.name                                  AS source_name,
    e.title,
    e.summary,
    e.description,
    e.url,
    e.registration_url,
    e.image_url,
    e.starts_at,
    e.ends_at,
    e.all_day,
    e.location,
    e.organizer,
    e.language,
    coalesce(t.tags, '[]'::jsonb)           AS tags,
    coalesce(t.tag_slugs, '{}'::text[])     AS tag_slugs,
    e.first_seen_at,
    e.updated_at,
    e.search_vector
FROM core.events  AS e
JOIN core.sources AS s ON s.id = e.source_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object('slug', tg.slug, 'label', tg.label) ORDER BY tg.label) AS tags,
           array_agg(tg.slug ORDER BY tg.slug)                                             AS tag_slugs
      FROM core.event_tags AS et
      JOIN core.tags       AS tg ON tg.id = et.tag_id
     WHERE et.event_id = e.id
) AS t ON true
WHERE e.is_active
  AND s.is_enabled;

COMMENT ON VIEW api.events IS 'All visible events (active + enabled source) with aggregated tags.';


-- -----------------------------------------------------------------------------
-- api.tags  -  for the filter chips. Counts only events that have not ended.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW api.tags AS
SELECT
    tg.slug,
    tg.label,
    count(e.id) FILTER (WHERE coalesce(e.ends_at, e.starts_at) >= now()) AS upcoming_count
FROM core.tags AS tg
LEFT JOIN core.event_tags AS et ON et.tag_id = tg.id
LEFT JOIN core.events     AS e  ON e.id = et.event_id AND e.is_active
LEFT JOIN core.sources    AS s  ON s.id = e.source_id
WHERE s.id IS NULL OR s.is_enabled
GROUP BY tg.slug, tg.label;

COMMENT ON VIEW api.tags IS 'Tag vocabulary with the number of upcoming visible events per tag.';


-- -----------------------------------------------------------------------------
-- api.sources  -  transparency + monitoring ("data last refreshed at ...").
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW api.sources AS
SELECT
    s.key,
    s.name,
    s.base_url,
    s.is_enabled,
    last_run.status      AS last_run_status,
    last_run.started_at  AS last_run_started_at,
    last_run.finished_at AS last_run_finished_at,
    (SELECT max(r.finished_at)
       FROM core.scrape_runs AS r
      WHERE r.source_id = s.id
        AND r.status IN ('succeeded', 'partial'))       AS last_success_at,
    (SELECT count(*)
       FROM core.events AS e
      WHERE e.source_id = s.id
        AND e.is_active
        AND coalesce(e.ends_at, e.starts_at) >= now()) AS upcoming_events
FROM core.sources AS s
LEFT JOIN LATERAL (
    SELECT r.status, r.started_at, r.finished_at
      FROM core.scrape_runs AS r
     WHERE r.source_id = s.id
     ORDER BY r.started_at DESC
     LIMIT 1
) AS last_run ON true;

COMMENT ON VIEW api.sources IS 'Scraped sources with the outcome of their most recent run.';


-- -----------------------------------------------------------------------------
-- api.filter_events
-- Single source of truth for filtering. Every argument is optional (NULL = no
-- restriction).
--
--   p_from     events that have NOT ENDED before this instant (default: now)
--              -> ongoing multi-day events stay visible until their last day
--   p_to       events that START before this instant
--   p_tags     tag slugs; an event matches if it has ANY of them
--   p_sources  source keys; ANY of them
--   p_query    free text, web-search syntax: "career fair" -online  OR
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION api.filter_events(
    p_from    timestamptz DEFAULT now(),
    p_to      timestamptz DEFAULT NULL,
    p_tags    text[]      DEFAULT NULL,
    p_sources text[]      DEFAULT NULL,
    p_query   text        DEFAULT NULL
)
RETURNS SETOF api.events
LANGUAGE sql
STABLE
AS $$
    SELECT e.*
      FROM api.events AS e
     WHERE (p_from    IS NULL OR coalesce(e.ends_at, e.starts_at) >= p_from)
       AND (p_to      IS NULL OR e.starts_at < p_to)
       AND (p_tags    IS NULL OR cardinality(p_tags) = 0    OR e.tag_slugs && p_tags)
       AND (p_sources IS NULL OR cardinality(p_sources) = 0 OR e.source_key = ANY (p_sources))
       AND (p_query   IS NULL OR btrim(p_query) = ''
            OR e.search_vector @@ websearch_to_tsquery('simple', p_query));
$$;

COMMENT ON FUNCTION api.filter_events(timestamptz, timestamptz, text[], text[], text) IS
    'Applies all public filters to api.events. NULL arguments mean "no restriction".';


-- -----------------------------------------------------------------------------
-- api.search_events  -  filtered, chronologically ordered, paginated.
-- Limit is clamped to 1..200 so a client cannot request the whole table.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION api.search_events(
    p_from    timestamptz DEFAULT now(),
    p_to      timestamptz DEFAULT NULL,
    p_tags    text[]      DEFAULT NULL,
    p_sources text[]      DEFAULT NULL,
    p_query   text        DEFAULT NULL,
    p_limit   integer     DEFAULT 50,
    p_offset  integer     DEFAULT 0
)
RETURNS SETOF api.events
LANGUAGE sql
STABLE
AS $$
    SELECT *
      FROM api.filter_events(p_from, p_to, p_tags, p_sources, p_query)
     ORDER BY starts_at, id
     LIMIT  least(greatest(coalesce(p_limit, 50), 1), 200)
     OFFSET greatest(coalesce(p_offset, 0), 0);
$$;

COMMENT ON FUNCTION api.search_events(timestamptz, timestamptz, text[], text[], text, integer, integer) IS
    'Filtered + paginated event list, ordered by start time.';


CREATE OR REPLACE FUNCTION api.count_events(
    p_from    timestamptz DEFAULT now(),
    p_to      timestamptz DEFAULT NULL,
    p_tags    text[]      DEFAULT NULL,
    p_sources text[]      DEFAULT NULL,
    p_query   text        DEFAULT NULL
)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT count(*) FROM api.filter_events(p_from, p_to, p_tags, p_sources, p_query);
$$;

COMMENT ON FUNCTION api.count_events(timestamptz, timestamptz, text[], text[], text) IS
    'Number of events matching the same filters as api.search_events.';

