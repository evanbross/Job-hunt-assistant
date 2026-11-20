/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
// The Job Queue page's lists, a page at a time from GET /api/jobs/queue*
// (routes/jobs.py). They used to be every queue and triage row (~2,400 on a
// real account, 2.3MB) filtered, sorted and rendered in the browser at once,
// a ~5s freeze. The server now sorts, searches and filters; this module
// fetches the first page, more on scroll, and keeps every visible list in
// step with the page's actions through JobQueueChangeBus.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type {
  Job,
  JobQueueCompaniesPage,
  JobQueueCompanyGroup,
  JobQueueCounts,
  JobQueueOptions,
  JobQueuePage,
  JobQueueViewKey,
} from "../../types/jobs.ts";

export const JOB_QUEUE_PAGE_SIZE = 50;
// The page routes' own maximum (routes/jobs.py _JOB_QUEUE_PAGE_SIZE_MAX): a
// reload that keeps more cards than this fetches them in several requests.
const MAX_PAGE_SIZE = 200;
const COUNTS_REFRESH_DELAY_MS = 300;

export interface JobQueueFilters {
  view: JobQueueViewKey;
  sort: string;
  search: string;
  searchField: string;
  onlyUnscored: boolean;
  onlyWithContacts: boolean;
  hideAppliedCompanies: boolean;
  /** One Company-view group's jobs (JobQueueCompanyGroup.company_key). */
  companyKey?: string | null;
}

/** The query string for a listing, shared by its page, counts and companies
 * requests so all three always describe the same rows. Unset toggles and an
 * empty search are left out, matching the routes' defaults. */
export function jobQueueQueryString(filters: JobQueueFilters, extra: Record<string, string | number> = {}): string {
  const params = new URLSearchParams({ view: filters.view, sort: filters.sort });
  if (filters.search.trim()) {
    params.set("search", filters.search);
    params.set("search_field", filters.searchField);
  }
  if (filters.onlyUnscored) params.set("only_unscored", "true");
  if (filters.onlyWithContacts) params.set("only_with_contacts", "true");
  if (filters.hideAppliedCompanies) params.set("hide_applied_companies", "true");
  if (filters.companyKey != null) params.set("company_key", filters.companyKey);
  for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
  return params.toString();
}

// ── Change bus ──────────────────────────────────────────────────────────────
// A successful action publishes what changed; every list on the page (the
// main listing, each open Company group, the group headers) applies it.

export type JobQueueChange =
  | { type: "remove"; jobId: number }
  | { type: "update"; job: Job }
  | { type: "reload" };

export class JobQueueChangeBus {
  private listeners = new Set<(change: JobQueueChange) => void>();

  subscribe(listener: (change: JobQueueChange) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  publish(change: JobQueueChange): void {
    for (const listener of [...this.listeners]) listener(change);
  }
}

export const JobQueueChangeContext = createContext<JobQueueChangeBus | null>(null);

// ai: claude-opus-5-5 | 2026-10-24
/** The page's change bus. Throws outside a JobQueueChangeContext provider: a
 * list with no bus to subscribe to would silently ignore every skip, approval
 * and update -- which is how the main Job Queue list shipped (the component
 * rendering the provider called these hooks itself, so it read null). */
export function useJobQueueChangeBus(): JobQueueChangeBus {
  const bus = useContext(JobQueueChangeContext);
  if (!bus) throw new Error("Job Queue lists must render inside a JobQueueChangeContext provider");
  return bus;
}

// ── Options ─────────────────────────────────────────────────────────────────

export function useJobQueueOptions(): JobQueueOptions | null {
  const [options, setOptions] = useState<JobQueueOptions | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/jobs/queue/options")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: JobQueueOptions | null) => {
        if (!cancelled && data) setOptions(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return options;
}

// ── Paged lists ─────────────────────────────────────────────────────────────

interface PagedListState<T> {
  items: T[];
  hasMore: boolean;
  /** False until the first page for the current view has arrived. */
  loaded: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  /** Re-fetch from the top, keeping as many items as are loaded now (and
   * the items themselves if the fetch fails). */
  reload: () => void;
  setItems: (update: (items: T[]) => T[]) => void;
}

interface PageResult<T> {
  items: T[];
  hasMore: boolean;
  nextCursor: string | null;
}

// ai: claude-opus-5-5 | 2026-11-03 — pages continue from the server's next_cursor, not an offset
/** Fetches *path* a page at a time. A new query string starts over, but keeps
 * showing the old items until the new ones arrive unless the view changed
 * (a different list entirely); a response for an older query is dropped.
 *
 * Each next page continues from the previous response's next_cursor (keyset
 * pagination, backend/db/keyset_pagination.py), never from how many items are
 * on screen: an offset counts rows from the top, so a job that left the list or
 * moved down between requests made the next page skip one. */
function usePagedList<T>(
  path: string,
  queryString: string,
  view: JobQueueViewKey,
  enabled: boolean,
  readPage: (data: unknown) => PageResult<T>,
  idOf: (item: T) => string | number,
  firstPageExtra: Record<string, string | number> = {},
): PagedListState<T> {
  const [items, setItemsState] = useState<T[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const generationRef = useRef(0);
  const itemsRef = useRef<T[]>([]);
  const loadingMoreRef = useRef(false);
  // Where the next loadMore continues: the last loaded page's next_cursor.
  const nextCursorRef = useRef<string | null>(null);
  const viewRef = useRef(view);
  const readPageRef = useRef(readPage);
  const idOfRef = useRef(idOf);
  const firstPageExtraKey = new URLSearchParams(
    Object.entries(firstPageExtra).map(([key, value]) => [key, String(value)]),
  ).toString();

  useEffect(() => {
    readPageRef.current = readPage;
    idOfRef.current = idOf;
  });

  const applyItems = useCallback((next: T[]) => {
    itemsRef.current = next;
    setItemsState(next);
  }, []);

  const setItems = useCallback((update: (current: T[]) => T[]) => {
    applyItems(update(itemsRef.current));
  }, [applyItems]);

  const fetchPage = useCallback(async (limit: number, cursor: string | null, extra: string) => {
    const params = new URLSearchParams(queryString);
    params.set("limit", String(limit));
    if (cursor != null) params.set("cursor", cursor);
    const res = await fetch(`${path}?${params}${extra ? `&${extra}` : ""}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return readPageRef.current(await res.json());
  }, [path, queryString]);

  // ai: claude-opus-5-5 | 2026-11-20 -- keepOnError: a failed reload (often a
  // background one, on a server signal) leaves the list as it was, not empty
  const loadFrom = useCallback(async (depth: number, extra: string, keepOnError = false) => {
    const generation = ++generationRef.current;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    try {
      const collected: T[] = [];
      const seen = new Set<string | number>();
      let cursor: string | null = null;
      let more = true;
      while (more && collected.length < depth) {
        const limit = Math.min(MAX_PAGE_SIZE, depth - collected.length);
        // A focused job can extend the first page past `limit` (see routes/jobs.py).
        const page: PageResult<T> = await fetchPage(limit, cursor, cursor == null ? extra : "");
        if (generation !== generationRef.current) return;
        for (const item of page.items) {
          const id = idOfRef.current(item);
          if (!seen.has(id)) {
            seen.add(id);
            collected.push(item);
          }
        }
        cursor = page.nextCursor;
        more = page.hasMore && cursor != null;
        // A short page is the end of what to load now; loadMore continues from the cursor.
        if (page.items.length < limit) break;
      }
      nextCursorRef.current = more ? cursor : null;
      applyItems(collected);
      setHasMore(more);
    } catch {
      if (generation !== generationRef.current || keepOnError) return;
      nextCursorRef.current = null;
      applyItems([]);
      setHasMore(false);
    } finally {
      if (generation === generationRef.current) setLoaded(true);
    }
  }, [applyItems, fetchPage]);

  useEffect(() => {
    if (!enabled) return;
    if (viewRef.current !== view) {
      viewRef.current = view;
      applyItems([]);
      setLoaded(false);
    }
    loadFrom(JOB_QUEUE_PAGE_SIZE, firstPageExtraKey);
  }, [enabled, view, loadFrom, firstPageExtraKey, applyItems]);

  const loadMore = useCallback(async () => {
    const cursor = nextCursorRef.current;
    if (loadingMoreRef.current || cursor == null) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    const generation = generationRef.current;
    try {
      const page = await fetchPage(JOB_QUEUE_PAGE_SIZE, cursor, "");
      if (generation !== generationRef.current) return;
      // An item that moved down since its page loaded (re-scored, re-sorted)
      // comes back again after the cursor; keep the copy already on screen.
      const seen = new Set(itemsRef.current.map((item) => idOfRef.current(item)));
      applyItems([...itemsRef.current, ...page.items.filter((item) => !seen.has(idOfRef.current(item)))]);
      const more = page.hasMore && page.nextCursor != null;
      nextCursorRef.current = more ? page.nextCursor : null;
      setHasMore(more);
    } catch {
      /* leave the list as it is; the sentinel or button can try again */
    } finally {
      if (generation === generationRef.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [applyItems, fetchPage]);

  const reload = useCallback(() => {
    loadFrom(Math.max(JOB_QUEUE_PAGE_SIZE, itemsRef.current.length), "", true);
  }, [loadFrom]);

  return { items, hasMore, loaded, loadingMore, loadMore, reload, setItems };
}

function readNextCursor(page: { next_cursor?: unknown } | null | undefined): string | null {
  return typeof page?.next_cursor === "string" ? page.next_cursor : null;
}

function readJobPage(data: unknown): PageResult<Job> {
  const page = data as JobQueuePage;
  return {
    items: Array.isArray(page?.jobs) ? page.jobs : [],
    hasMore: Boolean(page?.has_more),
    nextCursor: readNextCursor(page),
  };
}

function readCompaniesPage(data: unknown): PageResult<JobQueueCompanyGroup> {
  const page = data as JobQueueCompaniesPage;
  return {
    items: Array.isArray(page?.companies) ? page.companies : [],
    hasMore: Boolean(page?.has_more),
    nextCursor: readNextCursor(page),
  };
}

const jobId = (job: Job) => job.id;
const companyKey = (group: JobQueueCompanyGroup) => group.company_key;

// ai: claude-opus-5-5 | 2026-10-24
// A published update is a card action's own response, which -- unlike a page
// -- carries no company summary (backend/job_queue_company_summary.py). The
// listed copy's still holds while the company is unchanged; after a company
// edit it describes the old employer, so the list reloads instead.
function sameCompany(listed: Job, updated: Job): boolean {
  return listed.company === updated.company && listed.company_normalized === updated.company_normalized;
}

function withCompanySummary(listed: Job, updated: Job): Job {
  return {
    ...updated,
    company_job_count: updated.company_job_count ?? listed.company_job_count,
    company_application_statuses: updated.company_application_statuses ?? listed.company_application_statuses,
    company_contacts: updated.company_contacts ?? listed.company_contacts,
  };
}

export interface JobQueueListing {
  jobs: Job[];
  hasMore: boolean;
  loaded: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  /** Null until the first counts response (only when `withCounts`). */
  counts: JobQueueCounts | null;
}

/** One list of jobs, plus (with `withCounts`) the page's counts for the same
 * filters -- fetched even while the list itself is disabled, as in the
 * Company view, where group headers stand in for it but the toolbar's counts
 * and tab badges still apply. Applies every change published on JobQueueChangeContext:
 * a removed job leaves; an updated job is replaced in place while its status
 * still belongs to this view (per `viewStatuses`; null for Unreachable, which
 * keeps it) and leaves otherwise -- keeping its company summary, or reloading
 * the list if its company changed; "reload" starts over. A job this list hasn't
 * loaded is left for the next page or reload, since only the server knows
 * where it sorts. */
export function useJobQueueListing(
  filters: JobQueueFilters,
  { enabled = true, focusJobId = null, viewStatuses, withCounts = false }: {
    enabled?: boolean;
    focusJobId?: number | null;
    viewStatuses: string[] | null | undefined;
    withCounts?: boolean;
  },
): JobQueueListing {
  const bus = useJobQueueChangeBus();  // ai: claude-opus-5-5 | 2026-10-24
  const queryString = jobQueueQueryString(filters);
  const list = usePagedList<Job>(
    "/api/jobs/queue",
    queryString,
    filters.view,
    enabled,
    readJobPage,
    jobId,
    focusJobId != null ? { focus_job_id: focusJobId } : {},
  );
  const [counts, setCounts] = useState<JobQueueCounts | null>(null);
  const countsGenerationRef = useRef(0);
  const countsTimerRef = useRef<number | undefined>(undefined);
  const viewStatusesRef = useRef(viewStatuses);

  useEffect(() => {
    viewStatusesRef.current = viewStatuses;
  }, [viewStatuses]);

  const refreshCounts = useCallback(async () => {
    if (!withCounts) return;
    const generation = ++countsGenerationRef.current;
    try {
      const res = await fetch(`/api/jobs/queue/counts?${queryString}`);
      if (!res.ok) return;
      const data: JobQueueCounts = await res.json();
      if (generation === countsGenerationRef.current) setCounts(data);
    } catch {
      /* keep the last counts */
    }
  }, [queryString, withCounts]);

  useEffect(() => {
    refreshCounts();
  }, [refreshCounts]);

  useEffect(() => () => window.clearTimeout(countsTimerRef.current), []);

  const { setItems, reload } = list;
  useEffect(() => {
    return bus.subscribe((change) => {
      if (change.type === "reload") {
        if (enabled) reload();
        refreshCounts();
        return;
      }
      if (enabled && change.type === "remove") {
        setItems((current) => current.filter((job) => job.id !== change.jobId));
      } else if (enabled && change.type === "update") {
        const updated = change.job;
        const statuses = viewStatusesRef.current;
        const staysInView = statuses == null || statuses.includes(updated.status || "");
        let companyChanged = false;
        setItems((current) => (
          staysInView
            ? current.map((job) => {
              if (job.id !== updated.id) return job;
              companyChanged = !sameCompany(job, updated);
              return withCompanySummary(job, updated);
            })
            : current.filter((job) => job.id !== updated.id)
        ));
        if (companyChanged) reload();
      }
      // Several actions can land at once (a batch re-fetch); count once after.
      window.clearTimeout(countsTimerRef.current);
      countsTimerRef.current = window.setTimeout(refreshCounts, COUNTS_REFRESH_DELAY_MS);
    });
  }, [bus, enabled, reload, refreshCounts, setItems]);

  return {
    jobs: list.items,
    hasMore: list.hasMore,
    loaded: list.loaded,
    loadingMore: list.loadingMore,
    loadMore: list.loadMore,
    counts,
  };
}

export interface JobQueueCompanies {
  groups: JobQueueCompanyGroup[];
  hasMore: boolean;
  loaded: boolean;
  loadingMore: boolean;
  loadMore: () => void;
}

/** The Company view's group headers. Any published change can move a job
 * between groups or change a group's counts, so the loaded groups re-fetch
 * shortly after (keeping how many are loaded). */
export function useJobQueueCompanies(filters: JobQueueFilters, enabled: boolean): JobQueueCompanies {
  const bus = useJobQueueChangeBus();  // ai: claude-opus-5-5 | 2026-10-24
  const list = usePagedList<JobQueueCompanyGroup>(
    "/api/jobs/queue/companies",
    jobQueueQueryString(filters),
    filters.view,
    enabled,
    readCompaniesPage,
    companyKey,
  );
  const timerRef = useRef<number | undefined>(undefined);
  const { reload } = list;

  useEffect(() => {
    if (!enabled) return undefined;
    const unsubscribe = bus.subscribe(() => {
      window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(reload, COUNTS_REFRESH_DELAY_MS);
    });
    return () => {
      unsubscribe();
      window.clearTimeout(timerRef.current);
    };
  }, [bus, enabled, reload]);

  return {
    groups: list.items,
    hasMore: list.hasMore,
    loaded: list.loaded,
    loadingMore: list.loadingMore,
    loadMore: list.loadMore,
  };
}
