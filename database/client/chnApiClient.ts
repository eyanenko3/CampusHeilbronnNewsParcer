/**
 * Typed client + React Native hook for the Campus Heilbronn Events API.
 *
 * Maps 1:1 to the PostgreSQL `api.*` read layer (`database/migrations/003_api_read_layer.sql`)
 * exposed by `database/api_server.py` (or Firebase Hosting `/api/v1/**` rewrites).
 */

import { useCallback, useEffect, useState } from "react";

export interface EventTag {
  slug: string;
  label: string;
}

export interface CampusEvent {
  id: number;
  source_key: string;
  source_name: string;
  title: string;
  summary: string | null;
  description: string | null;
  /** Link to the original event page on the source website. */
  url: string;
  /** Direct sign-up / registration link (falls back to `url` when form is on-page). */
  registration_url: string | null;
  /** Optional hero/thumbnail image URL. */
  image_url: string | null;
  /** ISO-8601 timestamp with UTC offset, e.g. "2026-11-06T11:30:00+01:00". */
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
  location: string | null;
  organizer: string | null;
  language: string | null;
  tags: EventTag[];
  tag_slugs: string[];
  first_seen_at: string;
  updated_at: string;
}

export interface TagSummary {
  slug: string;
  label: string;
  upcoming_count: number;
}

export interface SourceSummary {
  key: string;
  name: string;
  base_url: string;
  is_enabled: boolean;
  last_run_status: "running" | "succeeded" | "partial" | "failed" | null;
  last_run_started_at: string | null;
  last_run_finished_at: string | null;
  last_success_at: string | null;
  upcoming_events: number;
}

export interface EventFilterParams {
  /** ISO-8601 lower bound, or `"all"` to include past events. Defaults to `now()` in SQL. */
  from?: string;
  /** ISO-8601 upper bound on `starts_at`. */
  to?: string;
  /** Tag slugs (`["current-students", "campus"]`); matches events having ANY of these tags. */
  tags?: string[];
  /** Source keys (`["tum_chn_events"]`); matches events from ANY of these sources. */
  sources?: string[];
  /** Full-text search query (supports phrases `"career fair"`, negation `-online`, `OR`). */
  q?: string;
  /** Page size (1..200, default 50). */
  limit?: number;
  /** Pagination offset (default 0). */
  offset?: number;
}

export interface PaginatedEventsResponse {
  total: number;
  limit: number;
  offset: number;
  items: CampusEvent[];
}

export class ChnEventsApiClient {
  private readonly baseUrl: string;

  /**
   * @param baseUrl Base URL of the API server or Firebase Hosting site,
   *                e.g. `"http://localhost:8080"` in local Docker or
   *                `"https://tum-chn-events-proto.web.app"` in production.
   */
  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async listEvents(filters: EventFilterParams = {}): Promise<PaginatedEventsResponse> {
    const query = new URLSearchParams();
    if (filters.from) query.set("from", filters.from);
    if (filters.to) query.set("to", filters.to);
    if (filters.tags?.length) query.set("tags", filters.tags.join(","));
    if (filters.sources?.length) query.set("sources", filters.sources.join(","));
    if (filters.q?.trim()) query.set("q", filters.q.trim());
    if (filters.limit != null) query.set("limit", String(filters.limit));
    if (filters.offset != null) query.set("offset", String(filters.offset));

    const qs = query.toString();
    const res = await fetch(`${this.baseUrl}/api/v1/events${qs ? `?${qs}` : ""}`);
    if (!res.ok) {
      throw new Error(`GET /api/v1/events failed with HTTP ${res.status}`);
    }
    return (await res.json()) as PaginatedEventsResponse;
  }

  async getEvent(id: number): Promise<CampusEvent> {
    const res = await fetch(`${this.baseUrl}/api/v1/events/${id}`);
    if (!res.ok) {
      throw new Error(`GET /api/v1/events/${id} failed with HTTP ${res.status}`);
    }
    return (await res.json()) as CampusEvent;
  }

  async listTags(): Promise<TagSummary[]> {
    const res = await fetch(`${this.baseUrl}/api/v1/tags`);
    if (!res.ok) {
      throw new Error(`GET /api/v1/tags failed with HTTP ${res.status}`);
    }
    const data = (await res.json()) as { items: TagSummary[] };
    return data.items;
  }

  async listSources(): Promise<SourceSummary[]> {
    const res = await fetch(`${this.baseUrl}/api/v1/sources`);
    if (!res.ok) {
      throw new Error(`GET /api/v1/sources failed with HTTP ${res.status}`);
    }
    const data = (await res.json()) as { items: SourceSummary[] };
    return data.items;
  }
}

/**
 * React / React Native hook for querying and filtering TUM Campus Heilbronn events.
 */
export function useCampusEvents(client: ChnEventsApiClient, initialFilters: EventFilterParams = {}) {
  const [filters, setFilters] = useState<EventFilterParams>(initialFilters);
  const [events, setEvents] = useState<CampusEvent[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [eventsRes, tagsRes] = await Promise.all([
        client.listEvents(filters),
        client.listTags(),
      ]);
      setEvents(eventsRes.items);
      setTotal(eventsRes.total);
      setTags(tagsRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [client, filters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    events,
    total,
    tags,
    filters,
    setFilters,
    loading,
    error,
    refresh,
  };
}
