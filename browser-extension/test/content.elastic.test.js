/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-29
// Regression coverage for jobs.elastic.co searches (2026-10-29): the page
// renders the search's own "Search Results" list above the department's full,
// unfiltered job accordions, and the generic link scan counted both (238
// detected for a 23-result search). Fixture markup mirrors the live page's
// structure as inspected that day, trimmed to a few rows.
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const SEARCH_URL = "https://jobs.elastic.co/jobs/department/engineering?q=software&size=n_20_n";
const DEPARTMENT_URL = "https://jobs.elastic.co/jobs/department/engineering";

function resultRow(slug, id, title) {
  return `<li class="sui-result"><div class="MuiGrid-root MuiGrid-container">`
    + `<div class="MuiGrid-root MuiGrid-item"><a class="job-title-link" href="/jobs/engineering/united-states/${slug}/${id}" aria-label="${title}">`
    + `<div class="MuiBox-root"><h3 class="MuiTypography-h5">${title}</h3></div></a></div></div></li>`;
}

const DEPARTMENT_ACCORDION = `
  <div id="container-engineering" class="MuiPaper-root MuiAccordion-root">
    <h2>Engineering Jobs (3 results)</h2>
    <div class="MuiCollapse-root MuiCollapse-vertical MuiCollapse-entered"><div class="MuiCollapse-wrapper"><div class="MuiCollapse-wrapperInner">
      <div id="container-engineering-content" class="MuiAccordion-region"><div class="MuiAccordionDetails-root">
        <ul class="sui-results-container">
          ${resultRow("senior-software-engineer", 8069178, "Senior Software Engineer")}
          ${resultRow("staff-site-reliability-engineer", 8000001, "Staff Site Reliability Engineer")}
          ${resultRow("engineering-manager-cloud", 8000002, "Engineering Manager - Cloud")}
        </ul>
      </div></div>
    </div></div></div>
  </div>`;

const SEARCH_HTML = `<html><body><main id="main-content" class="elastic-careers">
  <h1>Engineering Openings</h1>
  <h2>Search Results</h2>
  <p class="MuiTypography-body2">2 Results</p>
  <ul class="sui-results-container">
    ${resultRow("senior-software-engineer", 8069178, "Senior Software Engineer")}
    ${resultRow("principal-software-engineer-security-elasticsearch", 8082612, "Principal Software Engineer - Security - Elasticsearch")}
  </ul>
  <nav aria-label="Pagination"><ul>
    <li><button type="button" aria-current="true" aria-label="page 1">1</button></li>
    <li><button type="button" disabled aria-label="Go to next page"></button></li>
  </ul></nav>
  ${DEPARTMENT_ACCORDION}
</main></body></html>`;

const DEPARTMENT_HTML = `<html><body><main id="main-content" class="elastic-careers">
  <h1>Engineering Openings</h1>
  ${DEPARTMENT_ACCORDION}
</main></body></html>`;

describe("jobs.elastic.co search results", () => {
  it("counts and captures only the search's own results, not the department accordions", async () => {
    const page = loadContentScript({ url: SEARCH_URL, html: SEARCH_HTML });

    expect(await page.detectPage()).toMatchObject({ type: "elasticSearch", jobCount: 2 });
    const { payload, warning } = await page.build();

    expect(warning).toBeNull();
    expect(payload.jobs.map((job) => job.url)).toEqual([
      "https://jobs.elastic.co/jobs/engineering/united-states/senior-software-engineer/8069178",
      "https://jobs.elastic.co/jobs/engineering/united-states/principal-software-engineer-security-elasticsearch/8082612",
    ]);
    expect(payload.jobs[1].job_title).toBe("Principal Software Engineer - Security - Elasticsearch");
  });

  it("leaves a department page with no search on the generic whole-page capture", async () => {
    const page = loadContentScript({ url: DEPARTMENT_URL, html: DEPARTMENT_HTML });

    expect(await page.detectPage()).toMatchObject({ type: "smartGeneric", jobCount: 3 });
  });
});
