/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-28
// Regression coverage for builtin.com (found live, 2026-11-28): its "Go to
// Next Page" pagination link is a plain <a href="...&page=2">, which
// isNavigatingAnchor correctly refuses to click (a real navigation would
// tear down the content script mid-scan) -- but the old code treated "no
// clickable next control" as "no more pages, fall back to scrolling", which
// does nothing on a site that isn't infinite-scroll. It silently stopped at
// page 1 (confirmed live: reported 20 of ~2,000+ jobs). The fix fetches that
// same href's HTML directly instead of clicking it, never touching the live
// tab, and keeps following each fetched page's own next link.
import { describe, expect, it, vi } from "vitest";
import { htmlResponse, loadContentScript } from "./contentHarness.js";

const URL = "https://jobs.example.com/search?q=engineer";

function card(path, title, location) {
  return `<div class="job-card"><a href="${path}">${title}</a><span>${location}</span></div>`;
}

const PAGE_1 = `<html><body>
  ${card("/job/100-senior-engineer", "Senior Engineer", "Remote - USA")}
  ${card("/job/101-staff-engineer", "Staff Engineer", "Remote - USA")}
  <a href="/search?q=engineer&page=2" aria-label="Go to Next Page">2</a>
</body></html>`;

const PAGE_2 = `<html><body>
  ${card("/job/102-principal-engineer", "Principal Engineer", "Remote - USA")}
  <a href="/search?q=engineer&page=3" aria-label="Go to Next Page">3</a>
</body></html>`;

const PAGE_3 = `<html><body>
  ${card("/job/103-lead-engineer", "Lead Engineer", "Remote - USA")}
</body></html>`;

function builtinLikeFetchMock() {
  return vi.fn(async (url) => {
    const target = String(url);
    if (target.includes("page=2")) return htmlResponse(PAGE_2);
    if (target.includes("page=3")) return htmlResponse(PAGE_3);
    throw new Error(`unexpected fetch: ${target}`);
  });
}

describe("numbered pagination that fully reloads the page (builtin.com shape)", () => {
  it("counts jobs across every fetched page instead of stopping at page 1", async () => {
    const fetchMock = builtinLikeFetchMock();
    const page = loadContentScript({ url: URL, html: PAGE_1, fetchMock });

    const result = await page.detectPage();

    expect(result).toMatchObject({ type: "smartGeneric", jobCount: 4 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("page=2");
    expect(String(fetchMock.mock.calls[1][0])).toContain("page=3");
  });

  it("captures every job's url and title, including ones from fetched pages", async () => {
    const page = loadContentScript({ url: URL, html: PAGE_1, fetchMock: builtinLikeFetchMock() });

    const { payload } = await page.build();

    expect(
      payload.jobs.map((job) => ({ url: job.url.split("/").pop(), title: job.job_title }))
    ).toEqual([
      { url: "100-senior-engineer", title: "Senior Engineer" },
      { url: "101-staff-engineer", title: "Staff Engineer" },
      { url: "102-principal-engineer", title: "Principal Engineer" },
      { url: "103-lead-engineer", title: "Lead Engineer" },
    ]);
  });

  it("never clicks the navigating link itself", async () => {
    const fetchMock = builtinLikeFetchMock();
    const page = loadContentScript({ url: URL, html: PAGE_1, fetchMock });
    const { document } = page.window;
    const nextLink = document.querySelector('a[aria-label="Go to Next Page"]');
    let clicked = false;
    nextLink.addEventListener("click", () => { clicked = true; });

    await page.detectPage();

    expect(clicked).toBe(false);
  });

  it("stops cleanly when a fetch fails, keeping what it already found", async () => {
    const fetchMock = vi.fn(async () => { throw new Error("network error"); });
    const page = loadContentScript({ url: URL, html: PAGE_1, fetchMock });

    const result = await page.detectPage();

    expect(result).toMatchObject({ type: "smartGeneric", jobCount: 2 });
  });
});
