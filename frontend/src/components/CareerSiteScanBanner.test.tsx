/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// A career-site source the browser extension flagged as needing attention
// (backend/db/repositories/career_site_sources.py's flag_attention, set from
// a /capture-failed report that looks like a login wall) gets a distinct
// "Open" / "I've signed in" pair instead of the normal "Scan" / "Dismiss" --
// retrying the same automated capture again would just fail the same way.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CareerSiteScanBanner from "./CareerSiteScanBanner.tsx";
import type { CareerSite } from "../types/careerSites.ts";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

const FLAGGED_SITE: CareerSite = {
  id: 11,
  name: "LinkedIn 1",
  url: "https://www.linkedin.com/jobs/search-results/",
  enabled: 1,
  collection_mode: "automatic_browser",
  last_captured_at: "2026-11-20T22:20:42+00:00",
  recapture_interval_days: 4,
  needs_attention: 1,
  attention_reason: "Looks like a login or verification wall.",
};

const ORDINARY_DUE_SITE: CareerSite = {
  id: 6,
  name: "Wellfound - job board",
  url: "https://wellfound.com/jobs",
  enabled: 1,
  collection_mode: "automatic_browser",
  last_captured_at: "2026-11-18T00:00:00+00:00",
  recapture_interval_days: 4,
};

describe("CareerSiteScanBanner needs_attention row", () => {
  let dueSites: CareerSite[];
  let fetchMock: ReturnType<typeof vi.fn>;
  let openMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    dueSites = [FLAGGED_SITE];
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/career-sites/recapture-due") return Promise.resolve(jsonResponse(dueSites));
      if (url === "/api/career-scan/status") return Promise.resolve(jsonResponse({ running: false }));
      if (url === `/api/career-sites/${FLAGGED_SITE.id}/dismiss-recapture` && method === "POST") {
        dueSites = dueSites.filter((s) => s.id !== FLAGGED_SITE.id);
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    openMock = vi.fn();
    vi.stubGlobal("open", openMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the reason and an Open action instead of Scan", async () => {
    render(<CareerSiteScanBanner />);

    expect(await screen.findByText("Looks like a login or verification wall.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(openMock).toHaveBeenCalledWith(FLAGGED_SITE.url, "_blank", "noopener,noreferrer");
  });

  it("resumes automatic scanning (dismiss-recapture) when the user says they've signed in", async () => {
    render(<CareerSiteScanBanner />);
    fireEvent.click(await screen.findByRole("button", { name: "I've signed in" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/career-sites/${FLAGGED_SITE.id}/dismiss-recapture`,
        expect.objectContaining({ method: "POST" })
      )
    );
    await waitFor(() => expect(screen.queryByText("LinkedIn 1")).not.toBeInTheDocument());
  });

  it("leaves an ordinary due site's Scan/Dismiss controls unchanged", async () => {
    dueSites = [ORDINARY_DUE_SITE];
    render(<CareerSiteScanBanner />);

    expect(await screen.findByRole("button", { name: "Scan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "I've signed in" })).not.toBeInTheDocument();
  });
});
