/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  JobQueueChangeBus,
  JobQueueChangeContext,
  jobQueueQueryString,
  useJobQueueListing,
  type JobQueueFilters,
} from "./useJobQueueListing.ts";
import type { Job, JobQueueCounts } from "../../types/jobs.ts";

const FILTERS: JobQueueFilters = {
  view: "both",
  sort: "date_desc",
  search: "",
  searchField: "company",
  onlyUnscored: false,
  onlyWithContacts: false,
  hideAppliedCompanies: false,
};

const COUNTS: JobQueueCounts = {
  total_all: 3, total: 3, unscored: 0, with_contacts: 0, at_applied_company: 0,
  triage: 2, unreachable: 0, refreshable_triage: 0, unrated_refreshable: 0,
};

function job(id: number, status = "triage"): Job {
  return { id, company: `Company ${id}`, job_title: "Engineer", status };
}

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

type FetchMock = ReturnType<typeof vi.fn>;

interface FakePage {
  jobs: Job[];
  has_more: boolean;
  next_cursor?: string | null;
}

// ai: claude-opus-5-5 | 2026-11-03 — keyed by cursor (keyset pagination) instead of offset
/** Answers /counts with COUNTS and every page request from `pages`, keyed by
 * the `cursor` it sent ("" for a first page), recording each page request's
 * parsed query. */
function routeFetch(pages: Record<string, FakePage>) {
  const pageRequests: URLSearchParams[] = [];
  (fetch as unknown as FetchMock).mockImplementation((url: string) => {
    const [path, query] = url.split("?");
    const params = new URLSearchParams(query);
    if (path === "/api/jobs/queue/counts") return ok(COUNTS);
    pageRequests.push(params);
    const page = pages[params.get("cursor") ?? ""] ?? { jobs: [], has_more: false };
    return ok({ next_cursor: null, ...page });
  });
  return pageRequests;
}

function withBus(bus: JobQueueChangeBus) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <JobQueueChangeContext.Provider value={bus}>{children}</JobQueueChangeContext.Provider>;
  };
}

describe("jobQueueQueryString", () => {
  it("sends only what narrows the listing", () => {
    expect(jobQueueQueryString(FILTERS)).toBe("view=both&sort=date_desc");
    expect(jobQueueQueryString({ ...FILTERS, search: "  " })).toBe("view=both&sort=date_desc");
    const params = new URLSearchParams(jobQueueQueryString(
      { ...FILTERS, search: "acme", onlyUnscored: true, hideAppliedCompanies: true, companyKey: "acme" },
      { limit: 5 },
    ));
    expect(Object.fromEntries(params)).toEqual({
      view: "both", sort: "date_desc", search: "acme", search_field: "company",
      only_unscored: "true", hide_applied_companies: "true", company_key: "acme", limit: "5",
    });
  });
});

describe("useJobQueueListing", () => {
  let bus: JobQueueChangeBus;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    bus = new JobQueueChangeBus();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("loads the first page and the counts, then the next page from the server's cursor", async () => {
    const requests = routeFetch({
      "": { jobs: [job(1), job(2)], has_more: true, next_cursor: "after-2" },
      "after-2": { jobs: [job(2), job(3)], has_more: false },
    });
    const { result } = renderHook(
      () => useJobQueueListing(FILTERS, { viewStatuses: ["pending_review", "triage"], withCounts: true }),
      { wrapper: withBus(bus) },
    );

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.jobs.map((j) => j.id)).toEqual([1, 2]);
    expect(result.current.hasMore).toBe(true);
    await waitFor(() => expect(result.current.counts).toEqual(COUNTS));

    await act(async () => {
      result.current.loadMore();
    });
    await waitFor(() => expect(result.current.hasMore).toBe(false));
    // Job 2 came back on the second page (it moved); the copy on screen stays, once.
    expect(result.current.jobs.map((j) => j.id)).toEqual([1, 2, 3]);
    expect(requests.map((params) => params.get("cursor"))).toEqual([null, "after-2"]);
    expect(requests.every((params) => !params.has("offset"))).toBe(true);
  });

  // ai: claude-opus-5-5 | 2026-11-03 — code-review-2026-11-02 #1
  it("continues from the cursor even after jobs leave the list, instead of a count of what's on screen", async () => {
    const requests = routeFetch({
      "": { jobs: [job(1), job(2), job(3)], has_more: true, next_cursor: "after-3" },
      "after-3": { jobs: [job(4)], has_more: false },
    });
    const { result } = renderHook(
      () => useJobQueueListing({ ...FILTERS, view: "triage" }, { viewStatuses: ["triage"] }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));

    act(() => bus.publish({ type: "remove", jobId: 1 }));
    act(() => bus.publish({ type: "update", job: job(2, "pending_review") }));  // left the Triage view
    await act(async () => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.hasMore).toBe(false));
    expect(requests.map((params) => params.get("cursor"))).toEqual([null, "after-3"]);
    expect(result.current.jobs.map((j) => j.id)).toEqual([3, 4]);
  });

  it("does not ask for another page when the server gave no cursor", async () => {
    const requests = routeFetch({ "": { jobs: [job(1)], has_more: true, next_cursor: null } });
    const { result } = renderHook(
      () => useJobQueueListing(FILTERS, { viewStatuses: null }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.hasMore).toBe(false);

    await act(async () => {
      result.current.loadMore();
    });
    expect(requests).toHaveLength(1);
  });

  it("drops a response for filters that have since changed", async () => {
    let resolveStale: (value: unknown) => void = () => {};
    (fetch as unknown as FetchMock).mockImplementation((url: string) => {
      if (url.startsWith("/api/jobs/queue/counts")) return ok(COUNTS);
      if (url.includes("search=old")) return new Promise((resolve) => { resolveStale = resolve; });
      return ok({ jobs: [job(9)], has_more: false });
    });
    const { result, rerender } = renderHook(
      ({ search }) => useJobQueueListing({ ...FILTERS, search }, { viewStatuses: null }),
      { initialProps: { search: "old" }, wrapper: withBus(bus) },
    );

    rerender({ search: "new" });
    await waitFor(() => expect(result.current.jobs.map((j) => j.id)).toEqual([9]));
    await act(async () => {
      resolveStale({ ok: true, status: 200, json: () => Promise.resolve({ jobs: [job(1)], has_more: false }) });
    });
    expect(result.current.jobs.map((j) => j.id)).toEqual([9]);
  });

  it("applies published changes by the view's statuses, and reloads to the same depth", async () => {
    const requests = routeFetch({
      "": { jobs: [job(1), job(2), job(3)], has_more: false },
    });
    const { result } = renderHook(
      () => useJobQueueListing({ ...FILTERS, view: "triage" }, { viewStatuses: ["triage", "possible_duplicate"] }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));

    act(() => bus.publish({ type: "update", job: { ...job(1), job_title: "Renamed" } }));
    expect(result.current.jobs[0].job_title).toBe("Renamed");

    act(() => bus.publish({ type: "update", job: job(2, "pending_review") }));  // left the Triage view
    expect(result.current.jobs.map((j) => j.id)).toEqual([1, 3]);

    act(() => bus.publish({ type: "remove", jobId: 3 }));
    expect(result.current.jobs.map((j) => j.id)).toEqual([1]);

    act(() => bus.publish({ type: "update", job: job(42) }));  // never loaded: left for the server
    expect(result.current.jobs.map((j) => j.id)).toEqual([1]);

    await act(async () => bus.publish({ type: "reload" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].get("cursor")).toBeNull();  // a reload starts from the top
    expect(requests[1].get("limit")).toBe("50");
  });

  // ai: claude-opus-5-5 | 2026-10-24
  // ai: claude-opus-5-5 | 2026-11-20
  it("keeps the loaded jobs when a reload fails", async () => {
    const requests = routeFetch({ "": { jobs: [job(1), job(2)], has_more: false } });
    const { result } = renderHook(
      () => useJobQueueListing(FILTERS, { viewStatuses: ["pending_review", "triage"] }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));

    (fetch as unknown as FetchMock).mockImplementation(() => Promise.reject(new TypeError("network down")));
    await act(async () => bus.publish({ type: "reload" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(requests.length + 1));

    expect(result.current.jobs.map((j) => j.id)).toEqual([1, 2]);
  });

  it("keeps a listed job's company summary across an update, and reloads when its company changes", async () => {
    const summary = { company_job_count: 3, company_application_statuses: { applied: 2 }, company_contacts: [] };
    const requests = routeFetch({ "": { jobs: [{ ...job(1), ...summary }], has_more: false } });
    const { result } = renderHook(
      () => useJobQueueListing({ ...FILTERS, view: "triage" }, { viewStatuses: ["triage"] }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));

    act(() => bus.publish({ type: "update", job: { ...job(1), job_title: "Renamed" } }));
    expect(result.current.jobs[0]).toMatchObject({ job_title: "Renamed", ...summary });
    expect(requests).toHaveLength(1);

    await act(async () => bus.publish({ type: "update", job: { ...job(1), company: "Elsewhere" } }));
    await waitFor(() => expect(requests).toHaveLength(2));
  });

  // ai: claude-opus-5-5 | 2026-10-24
  it("refuses to run outside a change-bus provider instead of never subscribing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useJobQueueListing(FILTERS, { viewStatuses: null }))).toThrow(/JobQueueChangeContext/);
  });

  it("keeps an updated job whatever its status in the Unreachable view", async () => {
    routeFetch({ "": { jobs: [job(1, "applied")], has_more: false } });
    const { result } = renderHook(
      () => useJobQueueListing({ ...FILTERS, view: "unreachable" }, { viewStatuses: null }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.loaded).toBe(true));

    act(() => bus.publish({ type: "update", job: job(1, "skipped") }));
    expect(result.current.jobs.map((j) => [j.id, j.status])).toEqual([[1, "skipped"]]);
  });

  it("starts a new view empty instead of showing the old view's jobs", async () => {
    (fetch as unknown as FetchMock).mockImplementation((url: string) => {
      if (url.includes("view=queue")) return new Promise(() => {});
      return ok({ jobs: [job(1)], has_more: false });
    });
    const { result, rerender } = renderHook(
      ({ view }) => useJobQueueListing({ ...FILTERS, view }, { viewStatuses: undefined }),
      { initialProps: { view: "triage" as JobQueueFilters["view"] }, wrapper: withBus(bus) },
    );
    await waitFor(() => expect(result.current.jobs).toHaveLength(1));

    rerender({ view: "queue" });
    expect(result.current.jobs).toEqual([]);
    expect(result.current.loaded).toBe(false);
  });

  it("fetches counts but no page while the list is disabled", async () => {
    const requests = routeFetch({ "": { jobs: [job(1)], has_more: false } });
    const { result } = renderHook(
      () => useJobQueueListing({ ...FILTERS, view: "company" }, { enabled: false, viewStatuses: undefined, withCounts: true }),
      { wrapper: withBus(bus) },
    );

    await waitFor(() => expect(result.current.counts).toEqual(COUNTS));
    expect(requests).toHaveLength(0);
    expect(result.current.loaded).toBe(false);
  });

  it("asks the first page to reach a focused job", async () => {
    const requests = routeFetch({ "": { jobs: [job(1)], has_more: false } });
    renderHook(
      () => useJobQueueListing(FILTERS, { focusJobId: 77, viewStatuses: undefined }),
      { wrapper: withBus(bus) },
    );
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].get("focus_job_id")).toBe("77");
  });
});
