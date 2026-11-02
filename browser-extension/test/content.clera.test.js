/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-02
// Clera's job search is a split view whose cards are <button>s, not links, so
// the generic link scan used to offer the page as a single job. Card markup and
// API shapes mirror getclera.com/dashboard/jobs as inspected live (logged in)
// that day: POST /api/talents/me/external-jobs pages the list by cursor, and
// GET /api/jobs/external/<nanoId> has the full description.
import { describe, expect, it, vi } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const ORIGIN = "https://www.getclera.com";
const SEARCH_URL = `${ORIGIN}/dashboard/jobs?country=United+States&experience=senior&fit=0`;
const SEARCH = { filters: { countries: ["United States"], experienceLevel: ["5-10", "10+"] }, sort: "relevance", limit: 25 };

const listItem = (nanoId, position, companyName, applyUrl) => ({
  nanoId,
  position,
  companyName,
  locationLabel: "United States",
  workArrangement: "remote",
  remoteLocations: ["United States"],
  experienceLevel: "10+",
  salaryLowerBound: 171191,
  salaryUpperBound: 285319,
  salaryUnitText: "YEAR",
  currencyType: "USD",
  keySkills: ["Distributed systems"],
  applyUrl,
  matchScore: 92,
});

const JOBS = [
  listItem("5ujvrex5ddlj", "Staff Software Engineer", "Slate Auto", "https://recar.wd108.myworkdayjobs.com/SLATEcareers/job/Remote-WA/Staff-Software-Engineer_R1"),
  listItem("j46hoe8l60bo", "Senior Software Engineer II", "Honeycomb.io", "https://job-boards.greenhouse.io/honeycomb/jobs/5373987008"),
  listItem("q9x2k1m0zz77", "Principal Software Engineer", "Pantheon Systems", "https://pantheon.io/about/careers/detail?gh_jid=7201"),
];

const card = (job, current) => `
  <article data-job-id="${job.nanoId}" class="relative flex flex-col">
    <h3><button data-job-select="" aria-current="${current}">${job.position}</button></h3>
    <span>${job.companyName}</span><span>92% match</span>
  </article>`;

function searchPage({ recorded = SEARCH, selected = 0 } = {}) {
  const attr = recorded ? ` data-jha-clera-search='${JSON.stringify(recorded)}'` : "";
  return `<html${attr}><body>
    <nav><a href="/dashboard/jobs">Jobs</a></nav>
    <p>3 open roles</p>
    <div>${JOBS.slice(0, 2).map((job, i) => card(job, i === selected)).join("")}</div>
    <button>Load more · 2 of 3</button>
    <section><h1>${JOBS[selected].position}</h1><p>Detail panel text</p></section>
  </body></html>`;
}

function cleraFetch() {
  return vi.fn(async (url, init = {}) => {
    const { pathname } = new URL(url);
    if (pathname === "/api/talents/me/external-jobs" && init.method === "POST") {
      const body = JSON.parse(init.body);
      const page = body.cursor === "page-2"
        ? { data: [JOBS[2]], has_more: false, next_cursor: null, found: 3 }
        : { data: JOBS.slice(0, 2), has_more: true, next_cursor: "page-2", found: 3 };
      return { ok: true, status: 200, json: async () => page };
    }
    const detail = pathname.match(/^\/api\/jobs\/external\/(\w+)$/);
    if (detail) {
      const job = JOBS.find((j) => j.nanoId === detail[1]);
      return { ok: true, status: 200, json: async () => ({ job: { ...job, descriptionHtml: `<p>Full posting for ${job.position}.</p>` } }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
}

describe("Clera job search (getclera.com/dashboard/jobs)", () => {
  it("detects the whole search, not a single job", async () => {
    const page = await loadContentScript({ url: SEARCH_URL, html: searchPage() }).detectPage();
    expect(page).toMatchObject({ type: "cleraSplit", site: "Clera", jobCount: 3, captureModes: ["detail", "list"] });
  });

  it("detail mode captures just the job open on the right, under its ATS URL", async () => {
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ selected: 1 }), fetchMock: cleraFetch() });
    const { payload } = await ctx.build({ captureMode: "detail" });

    expect(payload.jobs).toHaveLength(1);
    const [job] = payload.jobs;
    expect(job).toMatchObject({
      url: "https://job-boards.greenhouse.io/honeycomb/jobs/5373987008",
      job_title: "Senior Software Engineer II",
      company: "Honeycomb.io",
    });
    expect(job.listing_text).toContain("Full posting for Senior Software Engineer II.");
    expect(job.listing_text).toContain("Salary: 171,191 - 285,319 USD per year");
    expect(job.listing_text).toContain(`Found on Clera: ${ORIGIN}/dashboard/jobs?job=j46hoe8l60bo`);
  });

  it("list mode pages the recorded search and fetches every job's full posting", async () => {
    const fetchMock = cleraFetch();
    const { payload, warning } = await loadContentScript({ url: SEARCH_URL, html: searchPage(), fetchMock }).build({ captureMode: "list" });

    expect(payload.jobs.map((j) => j.job_title)).toEqual(JOBS.map((j) => j.position));
    payload.jobs.forEach((job) => expect(job.listing_text).toContain(`Full posting for ${job.job_title}.`));
    expect(warning).toBeNull();
    const searchBodies = fetchMock.mock.calls
      .filter(([url, init]) => url.endsWith("/api/talents/me/external-jobs") && init?.method === "POST")
      .map(([, init]) => JSON.parse(init.body));
    expect(searchBodies).toEqual([SEARCH, { ...SEARCH, cursor: "page-2" }]);
  });

  it("without a recorded search, captures the loaded cards and says what's missing", async () => {
    const ctx = loadContentScript({ url: SEARCH_URL, html: searchPage({ recorded: null }), fetchMock: cleraFetch() });
    const { payload, warning } = await ctx.build({ captureMode: "list" });

    expect(payload.jobs.map((j) => j.job_title)).toEqual(["Staff Software Engineer", "Senior Software Engineer II"]);
    expect(warning).toMatch(/Captured 2 of the 3 jobs.*Reload this page/);
  });
});
