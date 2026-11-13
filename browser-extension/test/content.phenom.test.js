/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-28
// Regression coverage for the 2026-10-28 CVS Health capture bug: a filtered
// Phenom search (52 shown) sent the app only the 10 jobs on screen, each with
// just its ~200-character teaser, so all 10 were discarded as too short.
// Fixture shapes mirror jobs.cvshealth.com as inspected live that day.
import { describe, expect, it, vi } from "vitest";
import { htmlResponse, loadContentScript } from "./contentHarness.js";

const ORIGIN = "https://jobs.cvshealth.com";
const SEARCH_URL = `${ORIGIN}/us/en/search-results?keywords=software`;

function phAppScript() {
  // Site config first, then the SSR'd (unfiltered) first page -- the same
  // order as the live phApp bootstrap script.
  const ddo = { eagerLoadRefineSearch: { totalHits: 194, data: { jobs: [{ jobId: "R0000001", country: "United States" }] } } };
  return `<script>phApp = {"refNum":"CVSCHLUS","pageId":"page10","siteType":"external","country":"us","locale":"en_US","deviceType":"desktop","pageName":"search-results","csrfToken":"tok123"};
    phApp.ddo = ${JSON.stringify(ddo)};</script>`;
}

// ai: claude-opus-5-5 | 2026-10-29 -- recorded: what phenomSearchRecorder.js
// left on <html data-jha-phenom-search> (the page's own latest search request).
function searchPage({ resultsFound, recorded = null }) {
  const recordedAttr = recorded ? ` data-jha-phenom-search='${JSON.stringify(recorded).replace(/'/g, "&#39;")}'` : "";
  return `<html${recordedAttr}><head><title>Search CVS Health Jobs | Find Your Next Career Opportunity</title>${phAppScript()}</head>
    <body>
      <input type="checkbox" data-ph-at-id="facet-checkbox" data-ph-at-facetkey="facet-remote" data-ph-at-text="Remote" checked>
      <input type="checkbox" data-ph-at-id="facet-checkbox" data-ph-at-facetkey="facet-remote" data-ph-at-text="Hybrid">
      <input type="checkbox" data-ph-at-id="facet-checkbox" data-ph-at-facetkey="facet-category" data-ph-at-text="Innovation and Technology" checked>
      <p>${resultsFound} results found</p>
    </body></html>`;
}

function listedJob(n) {
  return {
    jobId: `R10${String(n).padStart(5, "0")}`,
    title: `Software Engineer ${n}`,
    location: "Work at Home, Illinois, United States",
    remote: "Remote",
    descriptionTeaser: `Teaser ${n}.`,
  };
}

function jobPage(title) {
  const ld = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title,
    description: `<p>Full description of ${title}.</p>`,
    hiringOrganization: { "@type": "Organization", name: "CVS Health" },
  };
  return `<html><head><script type="application/ld+json">${JSON.stringify(ld)}</script></head><body></body></html>`;
}

const ALL_JOBS = Array.from({ length: 12 }, (_, i) => listedJob(i + 1));

function widgetsFetch({ totalHits = ALL_JOBS.length } = {}) {
  return vi.fn(async (url, init) => {
    if (url === `${ORIGIN}/widgets`) {
      const body = JSON.parse(init.body);
      const jobs = ALL_JOBS.slice(body.from, body.from + body.size);
      return htmlResponse(JSON.stringify({ refineSearch: { status: 200, totalHits, data: { jobs } } }));
    }
    const job = ALL_JOBS.find((j) => url.includes(`/job/${j.jobId}/`));
    return job ? htmlResponse(jobPage(job.title)) : htmlResponse("", { ok: false, status: 404 });
  });
}

describe("Phenom search page with filters ticked (jobs.cvshealth.com)", () => {
  it("reports the filtered total from the widgets API", async () => {
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ resultsFound: 12 }), fetchMock: widgetsFetch() });
    expect(await ctx.detectPage()).toMatchObject({ type: "phenomSearch", jobCount: 12 });
  });

  it("captures every filtered job, each with its full description", async () => {
    const fetchMock = widgetsFetch();
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ resultsFound: 12 }), fetchMock });
    const { payload } = await ctx.build();

    const widgetCalls = fetchMock.mock.calls.filter(([url]) => url === `${ORIGIN}/widgets`);
    // build re-runs page detection first, which asks for page 0 as well.
    expect(new Set(widgetCalls.map(([, init]) => JSON.parse(init.body).from))).toEqual(new Set([0, 10]));
    const [, firstInit] = widgetCalls[0];
    expect(firstInit.headers["X-CSRF-TOKEN"]).toBe("tok123");
    expect(JSON.parse(firstInit.body)).toMatchObject({
      ddoKey: "refineSearch",
      refNum: "CVSCHLUS",
      lang: "en_us",
      keywords: "software",
      selected_fields: { remote: ["Remote"], category: ["Innovation and Technology"] },
    });

    expect(payload.jobs).toHaveLength(12);
    const [first] = payload.jobs;
    expect(first).toMatchObject({
      url: `${ORIGIN}/us/en/job/R1000001/software-engineer-1`,
      job_title: "Software Engineer 1",
      company: "CVS Health",
    });
    expect(first.listing_text).toContain("Work Environment: Remote");
    expect(first.listing_text).toContain("Full description of Software Engineer 1.");
    expect(first.listing_text).not.toContain("Teaser 1.");
  }, 20000);

  it("keeps the teaser for a job whose page can't be fetched", async () => {
    const fetchMock = widgetsFetch();
    const listingAndTwoPages = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (url, init) =>
      url.includes("/job/R1000001/") ? htmlResponse("", { ok: false, status: 503 }) : listingAndTwoPages(url, init),
    );
    const { payload } = await loadContentScript({ url: SEARCH_URL, html: searchPage({ resultsFound: 12 }), fetchMock }).build();

    expect(payload.jobs[0].listing_text).toContain("Teaser 1.");
    expect(payload.jobs[1].listing_text).toContain("Full description of Software Engineer 2.");
  }, 20000);

  // ai: claude-opus-5-5 | 2026-10-29
  // The live 2026-10-29 CVS failure: a search rebuilt from the checkboxes
  // disagreed with the page's count, while the page's own request (with its
  // locationData radius) matched it.
  it("replays the page's own recorded search request, paging only from/size", async () => {
    const recorded = {
      csrfToken: "refreshed-token",
      body: {
        ddoKey: "eagerLoadRefineSearch",
        refNum: "CVSCHLUS",
        keywords: "software",
        selected_fields: { country: ["United States"], subCategory: ["Digital Engineering & Architecture"] },
        locationData: { sliderRadius: 50, aboveMaxRadius: true, LocationUnit: "miles" },
        jdsource: "facets",
        counts: true,
        from: 0,
        size: 10,
      },
    };
    const fetchMock = widgetsFetch();
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ resultsFound: 12, recorded }), fetchMock });
    const { payload, warning } = await ctx.build();

    expect(payload.jobs).toHaveLength(12);
    expect(warning).toBeNull();
    const widgetCalls = fetchMock.mock.calls.filter(([url]) => url === `${ORIGIN}/widgets`);
    for (const [, init] of widgetCalls) {
      expect(init.headers["X-CSRF-TOKEN"]).toBe("refreshed-token");
      const { from, ...rest } = JSON.parse(init.body);
      expect([0, 10]).toContain(from);
      expect(rest).toEqual({ ...recorded.body, from: undefined, ddoKey: "refineSearch", counts: false, jobs: true, size: 10 });
    }
  }, 20000);

  it("does not trust the API when its total disagrees with the page's own count", async () => {
    // e.g. a filter that neither the checkboxes nor a recorded request show
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ resultsFound: 7 }), fetchMock: widgetsFetch() });
    const page = await ctx.detectPage();
    expect(page.jobCount).toBe(7);
    expect(page.type).toBe("smartGeneric");
    // ai: claude-opus-5-5 | 2026-10-29 -- and says why, so a fallback is diagnosable
    expect(page.description).toContain("search API found 12, the page shows 7");
  });

  // ai: claude-opus-5-5 | 2026-10-29
  // What reached the app on 2026-10-29: the on-screen 10 of 52, sent silently.
  it("warns before sending a fallback capture short of the page's count", async () => {
    const cards = Array.from({ length: 3 }, (_, i) =>
      `<div><a href="${ORIGIN}/us/en/job/R200000${i}/engineer-${i}">Engineer ${i}</a><p>Remote, United States</p></div>`,
    ).join("");
    const html = searchPage({ resultsFound: 7 }).replace("</body>", `${cards}</body>`);
    const { payload, warning } = await loadContentScript({ url: SEARCH_URL, html, fetchMock: widgetsFetch() }).build();

    expect(payload.jobs.length).toBeLessThan(7);
    expect(warning).toContain(`Captured only ${payload.jobs.length} of the 7 jobs`);
  });
});
