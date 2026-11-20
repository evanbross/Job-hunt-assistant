/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-24
// The real JobQueue, not the listing hook under a test-supplied provider: the
// main list once read no change bus at all because JobQueue rendered the
// provider and called the hooks itself, so every skip left its card on screen
// -- invisible to hook tests, which always wrap the hook in a provider.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JobQueue from "./index.tsx";
import type { Job, JobQueueCounts, JobQueueOptions } from "../../types/jobs.ts";

const OPTIONS: JobQueueOptions = {
  sorts: [],
  search_fields: [],
  view_statuses: {
    queue: ["pending_review"],
    triage: ["triage", "possible_duplicate"],
    both: ["pending_review", "triage", "possible_duplicate"],
    company: null,
    unreachable: null,
  },
};

const COUNTS: JobQueueCounts = {
  total_all: 1, total: 1, unscored: 0, with_contacts: 0, at_applied_company: 0,
  triage: 1, unreachable: 0, refreshable_triage: 0, unrated_refreshable: 0,
};

const TITLE = "Senior Director Software Engineering";
const JOB: Job = { id: 6999, company: "Cotiviti", job_title: TITLE };

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

describe("JobQueue", () => {
  let skipped: string[];
  let approveBodies: unknown[];
  let page: Job[];

  beforeEach(() => {
    skipped = [];
    approveBodies = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = url.split("?")[0];
      if (path === "/api/jobs/queue/options") return ok(OPTIONS);
      if (path === "/api/jobs/queue/counts") return ok(COUNTS);
      if (path === "/api/jobs/queue") return ok({ jobs: page, has_more: false });
      if (init?.method === "POST" && /^\/api\/jobs\/\d+\/approve$/.test(path)) {
        approveBodies.push(JSON.parse(String(init.body)));
        return ok({ status: "ok" });
      }
      if (init?.method === "POST" && /^\/api\/jobs\/\d+\/skip$/.test(path)) {
        skipped.push(path);
        return ok({ status: "ok" });
      }
      return ok({});
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it.each([
    ["queue", "pending_review", /^Skip/],
    ["both", "triage", /^Not Relevant/],
    ["both", "possible_duplicate", /^Skip/],
  ])("takes a skipped job off the %s view's list (%s card)", async (view, status, skipButton) => {
    window.localStorage.setItem("jha.jobQueue.viewMode", view);
    page = [{ ...JOB, status }];
    render(<JobQueue isAuthenticated onNavigateToHistory={() => {}} />);
    await screen.findByText(TITLE);

    fireEvent.click(screen.getByRole("button", { name: skipButton }));
    fireEvent.click(screen.getByRole("button", { name: "Salary Too Low" }));

    await waitFor(() => expect(screen.queryByText(TITLE)).toBeNull());
    expect(skipped).toEqual([`/api/jobs/${JOB.id}/skip`]);
  });

  // ai: claude-opus-5-5 | 2026-11-19
  // The approve route's schema forbids extra fields; this card used to send a
  // fabricated sender_email too, so every Approve was rejected with a 400.
  it("approves a triage job with only the subject pattern the route accepts", async () => {
    window.localStorage.setItem("jha.jobQueue.viewMode", "both");
    page = [{ ...JOB, status: "triage" }];
    render(<JobQueue isAuthenticated onNavigateToHistory={() => {}} />);
    await screen.findByText(TITLE);

    fireEvent.click(screen.getByRole("button", { name: "It's a Job Posting" }));

    await waitFor(() => expect(screen.queryByText(TITLE)).toBeNull());
    expect(approveBodies).toEqual([{ subject_pattern: "senior director software engineering" }]);
  });
});
