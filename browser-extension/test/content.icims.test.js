/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
// Regression coverage for the 2026-10-23 iCIMS fix (commit 2caff38): classic
// iCIMS portals render the job list inside a same-origin
// #icims_content_iframe, so the top frame used to fall through to the generic
// page types and send the empty wrapper as one "Untitled job". Fixture markup
// mirrors careers-cotiviti.icims.com as inspected live that day.
import { describe, expect, it, vi } from "vitest";
import { htmlResponse, loadContentScript } from "./contentHarness.js";

const ORIGIN = "https://careers-cotiviti.icims.com";
const SEARCH_URL = `${ORIGIN}/jobs/search?ss=1&searchKeyword=software+engineer`;

function card({ id, slug, title, location = "US-Remote" }) {
  return `
    <li class="iCIMS_JobCardItem">
      <div class="row">
        <div class="col-xs-6 header left">
          <span class="sr-only field-label">Job Locations</span> <span> ${location}</span>
        </div>
        <div class="col-xs-6 header right">
          <span class="sr-only field-label">ID</span> <span> 2026-${id}</span>
        </div>
        <div class="col-xs-12 title">
          <a href="${ORIGIN}/jobs/${id}/${slug}/job?in_iframe=1" class="iCIMS_Anchor" title="${id} - ${title}">
            <span class="sr-only field-label">Title</span> <h3> ${title}</h3>
          </a>
        </div>
        <div class="col-xs-12 description">Card blurb for ${title}.</div>
      </div>
    </li>`;
}

// The "Welcome page" / "Log in" nav anchors share the iCIMS_Anchor class with
// the job links on the live page -- included so the path filter is exercised.
function searchFrameBody(cards) {
  return `
    <a href="${ORIGIN}/jobs/intro?in_iframe=1" class="iCIMS_Anchor iCIMS_Anchor_Nav">Welcome page</a>
    <a href="${ORIGIN}/jobs/login?in_iframe=1" class="iCIMS_Anchor iCIMS_Anchor_Nav">Log in</a>
    <div class="container-fluid iCIMS_JobsTable"><ul>${cards.join("")}</ul></div>`;
}

function jobPostingPage({ title, company = "Cotiviti", description }) {
  const ld = { "@context": "https://schema.org", "@type": "JobPosting", title, description, hiringOrganization: { "@type": "Organization", name: company } };
  return `<html><head><script type="application/ld+json">${JSON.stringify(ld)}</script></head>
    <body><div class="iCIMS_JobContent"><h1>${title}</h1></div></body></html>`;
}

const WRAPPER_HTML = `<html><head><title>Job Listings at Cotiviti</title></head>
  <body><header>Cotiviti careers</header><iframe id="icims_content_iframe"></iframe></body></html>`;

const CARDS = [
  card({ id: "20757", slug: "associate-software-engineer", title: "Associate Software Engineer" }),
  card({ id: "20162", slug: "principal-software-engineer", title: "Principal Software Engineer", location: "US-NJ-Jersey City" }),
];

function loadWrapperSearchPage(fetchMock) {
  return loadContentScript({
    url: SEARCH_URL,
    html: WRAPPER_HTML,
    iframes: { icims_content_iframe: searchFrameBody(CARDS) },
    fetchMock,
  });
}

describe("iCIMS search page (wrapper frame reading #icims_content_iframe)", () => {
  it("detects the jobs inside the iframe instead of the empty wrapper", async () => {
    const page = await loadWrapperSearchPage().detectPage();
    expect(page).toMatchObject({ type: "icimsSearch", jobCount: 2, site: "Cotiviti" });
  });

  it("captures one job per card, with full descriptions from each job's in_iframe=1 page", async () => {
    const fetchMock = vi.fn(async (url) => {
      const title = url.includes("/20757/") ? "Associate Software Engineer" : "Principal Software Engineer";
      return htmlResponse(jobPostingPage({ title, description: `<p>Full description of ${title}.</p>` }));
    });
    const { payload } = await loadWrapperSearchPage(fetchMock).build();

    // The bare job URL returns the wrapper page (no JSON-LD); only the
    // in_iframe=1 variant carries the JobPosting block.
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${ORIGIN}/jobs/20757/associate-software-engineer/job?in_iframe=1`,
      `${ORIGIN}/jobs/20162/principal-software-engineer/job?in_iframe=1`,
    ]);
    expect(payload.jobs).toHaveLength(2);

    const [first, second] = payload.jobs;
    // Stored URL is canonical (no in_iframe query) so re-captures dedupe.
    expect(first).toMatchObject({
      url: `${ORIGIN}/jobs/20757/associate-software-engineer/job`,
      job_title: "Associate Software Engineer",
      company: "Cotiviti",
    });
    expect(first.listing_text).toContain("Req ID: 2026-20757");
    expect(first.listing_text).toContain("Location: US-Remote");
    expect(first.listing_text).toContain("Full description of Associate Software Engineer.");
    expect(second.listing_text).toContain("Location: US-NJ-Jersey City");
    expect(payload.source.url).toBe(SEARCH_URL);
  }, 10000);

  it("falls back to card-level data when a job page fetch fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue(htmlResponse("", { ok: false, status: 503 }));
    const { payload } = await loadWrapperSearchPage(fetchMock).build();

    const [first] = payload.jobs;
    expect(first.job_title).toBe("Associate Software Engineer");
    // No JSON-LD hiringOrganization → company comes from the
    // careers-<company>.icims.com hostname, not "Careers Cotiviti".
    expect(first.company).toBe("Cotiviti");
    expect(first.listing_text).toContain("Card blurb for Associate Software Engineer.");
  }, 10000);
});

describe("iCIMS single job page (the content frame itself)", () => {
  const JOB_URL = `${ORIGIN}/jobs/20757/associate-software-engineer/job?in_iframe=1`;

  it("captures the one posting even when similar-jobs cards sit beside it", async () => {
    const posting = jobPostingPage({ title: "Associate Software Engineer", description: "<p>The real posting.</p>" });
    const html = posting.replace(
      "</body>",
      `<div class="iCIMS_JobsTable"><ul>${card({ id: "19311", slug: "principal-backend-engineer", title: "Principal Backend Engineer" })}</ul></div></body>`,
    );
    const ctx = loadContentScript({ url: JOB_URL, html });

    expect(await ctx.detectPage()).toMatchObject({ type: "icimsJob", jobCount: 1 });
    const { payload } = await ctx.build();
    expect(payload.jobs).toEqual([
      expect.objectContaining({
        url: `${ORIGIN}/jobs/20757/associate-software-engineer/job`,
        job_title: "Associate Software Engineer",
        company: "Cotiviti",
      }),
    ]);
    expect(payload.jobs[0].listing_text).toContain("The real posting.");
    expect(ctx.fetchMock).not.toHaveBeenCalled();
  });
});

describe("non-iCIMS pages", () => {
  it("does not treat a page without iCIMS markup as iCIMS", async () => {
    const ctx = loadContentScript({
      url: "https://example.com/careers",
      html: "<html><body><h1>Careers</h1><p>No openings.</p></body></html>",
    });
    const page = await ctx.detectPage();
    expect(page.type).not.toMatch(/^icims/);
  });
});
