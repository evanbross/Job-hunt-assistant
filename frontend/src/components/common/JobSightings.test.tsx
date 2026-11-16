/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-15
// "Seen N more times": nothing for a job with no sightings, a per-source summary
// for preloaded ones (the lead drawer), and a fetch on first open when only the
// count is known (a Job Queue card). Fixtures follow GET /api/jobs/<id>/sightings
// (routes/jobs.py's get_job_sightings).
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JobSightings from "./JobSightings.tsx";
import type { JobSighting } from "../../types/jobs.ts";

const SIGHTINGS: JobSighting[] = [
  {
    id: 3, job_id: 9, seen_at: "2026-11-14T08:00:00+00:00", source: "LinkedIn", channel: "email",
    capture_label: "New jobs similar to Staff Engineer", url: "https://www.linkedin.com/jobs/view/1/",
    sender_email: "jobs@linkedin.com",
  },
  {
    id: 2, job_id: 9, seen_at: "2026-11-10T08:00:00+00:00", source: "Wellfound", channel: "extension",
    capture_label: null, url: null, sender_email: null,
  },
  {
    id: 1, job_id: 9, seen_at: "2026-11-02T00:00:00+00:00", source: "LinkedIn", channel: "merged_duplicate",
    capture_label: null, url: "javascript:alert(1)", sender_email: null,
  },
];

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

describe("JobSightings", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ sightings: SIGHTINGS })));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing for a job with no sightings", () => {
    const { container } = render(<JobSightings jobId={9} count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("summarizes preloaded sightings per source without fetching", () => {
    render(<JobSightings jobId={9} sightings={SIGHTINGS} />);
    expect(screen.getByRole("button", { name: /Seen 3 more times/ })).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn ×2, Wellfound/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fetches on first open when only the count is known, and links only http(s) URLs", async () => {
    render(<JobSightings jobId={9} count={3} />);
    fireEvent.click(screen.getByRole("button", { name: /Seen 3 more times/ }));
    expect(await screen.findByText(/New jobs similar to Staff Engineer/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/jobs/9/sightings");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText(/earlier duplicate row/)).toBeInTheDocument();

    // Closing and reopening reuses what was fetched.
    fireEvent.click(screen.getByRole("button", { name: /Seen 3 more times/ }));
    fireEvent.click(screen.getByRole("button", { name: /Seen 3 more times/ }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
