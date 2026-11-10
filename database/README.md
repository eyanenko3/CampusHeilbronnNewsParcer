# `database/` — PostgreSQL Relational Schema, Ingestion & Filter Layer

SQL-first relational database architecture for the **TUM Campus Heilbronn** event aggregator. All referential integrity, deduplication, tag normalisation, soft-deletion, and search/filter logic live inside PostgreSQL schemas so both the Python scrapers and the React Native UI interact through strict, versioned contracts.

---

## 1. Three-Schema Architecture

| Schema | Role | Who Accesses It |
| :--- | :--- | :--- |
| **`core`** | Normalized relational tables (`sources`, `scrape_runs`, `events`, `tags`, `event_tags`) and `core.slugify()`. | Database owner / migrations only. |
| **`ingest`** | Write stored functions (`start_run`, `upsert_event`, `finish_run`) with `SECURITY DEFINER`. | Scraper service (`chn_ingest` role). |
| **`api`** | Read-only views (`events`, `tags`, `sources`) and filter/search functions (`filter_events`, `search_events`, `count_events`). | Read API & React Native app (`chn_reader` role). |

### Entity-Relationship Diagram

```mermaid
erDiagram
    SOURCES ||--o{ SCRAPE_RUNS : "logs execution"
    SOURCES ||--o{ EVENTS : "publishes"
    SCRAPE_RUNS ||--o{ EVENTS : "last_seen_run_id"
    EVENTS ||--o{ EVENT_TAGS : "categorised by"
    TAGS ||--o{ EVENT_TAGS : "assigned to"

    SOURCES {
        integer id PK
        text key UK "e.g. tum_chn_events"
        text name
        text base_url
        boolean is_enabled
    }

    SCRAPE_RUNS {
        bigint id PK
        integer source_id FK
        timestamptz started_at
        timestamptz finished_at
        text status "running | succeeded | partial | failed"
        integer events_seen
        integer inserted
        integer updated
        integer unchanged
        integer deactivated
        integer item_errors
    }

    EVENTS {
        bigint id PK
        integer source_id FK
        text external_id "UK with source_id"
        text title
        text summary
        text description
        text url "Original source URL"
        text registration_url
        text image_url
        timestamptz starts_at
        timestamptz ends_at
        boolean all_day
        text location
        text organizer
        text language
        jsonb extra
        text content_hash "md5 of canonical jsonb payload"
        boolean is_active
        bigint last_seen_run_id FK
        tsvector search_vector "Generated weighted FTS vector"
    }

    TAGS {
        integer id PK
        text slug UK "e.g. current-students"
        text label "e.g. Current Students"
    }

    EVENT_TAGS {
        bigint event_id PK, FK
        integer tag_id PK, FK
    }
```

---

## 2. Folder Structure

```text
database/
├── migrate.sh                         # Transactional SQL migration runner
├── api_server.py                      # Stateless HTTP JSON gateway over `api.*`
├── Dockerfile.api                     # Container image for the Read API
├── README.md                          # This guide
├── migrations/
│   ├── 001_core_schema.sql            # Tables, constraints, FTS tsvector, indexes
│   ├── 002_ingest_functions.sql       # Write API: start_run, upsert_event, finish_run
│   ├── 003_api_read_layer.sql         # Read API: views + filter_events, search_events, count_events
│   └── 004_roles_and_grants.sql       # Least-privilege roles: chn_ingest & chn_reader
├── scripts/
│   ├── create_app_users.sql           # Provisions login roles for scraper & API
│   ├── seed_sample_data.sql           # Sample events for offline/UI testing
│   └── deploy_firebase_cloudrun.sh    # Deploys containers + Firebase Hosting rewrites
├── client/
│   └── chnApiClient.ts                # Typed TypeScript SDK for React Native
└── public/
    └── index.html                     # Live web prototype hosted by Firebase Hosting
```

---

## 3. Write Contract (`ingest` Schema — `002_ingest_functions.sql`)

The Python parsers never execute raw `INSERT`/`UPDATE` statements on tables. Instead, `PostgresSink` calls three SQL functions:

1. **`ingest.start_run(p_source_key, p_source_name, p_base_url) -> bigint`**:
   - Automatically registers or updates the source in `core.sources`.
   - Marks any stale `running` run for that source as `failed`.
   - Opens and returns a new `scrape_runs.id`.
2. **`ingest.upsert_event(p_run_id, p_event jsonb) -> text`**:
   - Computes `md5(p_event::text)` (`jsonb` canonical text representation has deterministic key ordering).
   - Looks up `(source_id, external_id)`:
     - **New**: Inserts into `core.events` and syncs `core.tags` / `core.event_tags` $\rightarrow$ returns `'inserted'`.
     - **Changed hash**: Updates `core.events`, updates `updated_at`, and re-syncs tags $\rightarrow$ returns `'updated'`.
     - **Identical hash**: Only updates `last_seen_run_id` and `last_seen_at` (zero unnecessary disk churn) $\rightarrow$ returns `'unchanged'`.
3. **`ingest.finish_run(p_run_id, p_status, p_item_errors, p_error_message, p_complete) -> jsonb`**:
   - If and only if `p_status = 'succeeded'`, `p_complete = true`, and `events_seen > 0`, soft-deletes (`is_active = false`) any **upcoming** event of that source that was not seen in this run. Past events remain archived.

---

## 4. Read & Filter Contract (`api` Schema — `003_api_read_layer.sql`)

All filtering for the React Native app is centralized in SQL:

```sql
-- Example: upcoming events for "Current Students" or "Campus" mentioning "AI"
SELECT id, title, starts_at, ends_at, location, image_url, url, registration_url, tags
  FROM api.search_events(
      p_from    => now(),
      p_to      => NULL,
      p_tags    => ARRAY['current-students', 'campus'],
      p_sources => ARRAY['tum_chn_events'],
      p_query   => 'AI',
      p_limit   => 20,
      p_offset  => 0
  );
```

- **Ongoing multi-day events**: `coalesce(ends_at, starts_at) >= p_from` ensures an exhibition running Nov 6 – Dec 10 remains visible in upcoming results until Dec 10.
- **Tag filtering**: `e.tag_slugs && p_tags` matches events carrying any selected tag slug (`current-students`, `general-public`, `company`, etc.).
- **Weighted Full-Text Search**: `e.search_vector @@ websearch_to_tsquery('simple', p_query)` ranks matches across `title` (weight `A`), `summary` & `organizer` (`B`), `description` (`C`), and `location` (`D`).

---

## 5. Applying Migrations & Seeding

```bash
# Apply all pending migrations in database/migrations/*.sql
DATABASE_URL=postgresql://chn_owner:chn_owner_dev_pw@localhost:5432/chn_events \
  ./database/migrate.sh

# Optional: load sample CHN events without running the web scraper
psql postgresql://chn_owner:chn_owner_dev_pw@localhost:5432/chn_events \
  -v ON_ERROR_STOP=1 -f database/scripts/seed_sample_data.sql
```

