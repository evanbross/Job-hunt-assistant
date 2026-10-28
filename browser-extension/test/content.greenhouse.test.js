/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-27
// Regression coverage for Greenhouse's JS embed on a company's own careers
// page (redventures.com/careers/positions/open?gh_jid=8250089, 2026-10-27).
// The embed renders in a cross-origin job-boards.greenhouse.io iframe that the
// popup probes as its own frame; the board-page check used to claim both embed
// views as an empty "search" page, so the capture sent no jobs. Fixture markup
// mirrors the live embed iframes as fetched that day.
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const JOB_APP_URL = "https://job-boards.greenhouse.io/embed/job_app?for=redventures&token=8250089";
const JOB_BOARD_URL = "https://job-boards.greenhouse.io/embed/job_board?for=redventures";

const JOB_APP_HTML = `<html><head>
  <title>Job Application for Senior Software Engineer | Bankrate at Red Ventures</title></head>
  <body><main class="main font-secondary job-post"><div class="job__embedded-wrapper">
    <div class="job__header"><div class="job__title">
      <h1 class="section-header section-header--large font-primary">Senior Software Engineer | Bankrate</h1>
      <div class="job__location"><svg class="svg-icon"></svg><div>Charlotte, NC</div></div>
    </div></div>
    <div class="job__description body"><p>Bankrate is looking for a Senior Software Engineer to build our rate tables.</p></div>
    <div class="application--container">
      <h2 class="section-header font-primary">Apply for this job</h2>
      <div class="field-wrapper"><label>First Name*</label><input /></div>
      <div class="field-wrapper"><label>Voluntary Demographic Questions -- long application form text that must not be captured</label></div>
    </div>
  </div></main></body></html>`;

// No whitespace inside the anchor, as on the live page: jsdom's innerText
// stand-in (textContent) would otherwise start with a blank line.
function boardRow(jobId, title, where) {
  return `<tr class="job-post"><td class="cell">`
    + `<a href="https://www.redventures.com/careers/positions/open?gh_jid=${jobId}" target="_top">`
    + `<p class="body body--medium">${title}</p><p class="body body__secondary body--metadata">${where}</p>`
    + `</a></td></tr>`;
}

const JOB_BOARD_HTML = `<html><head><title>Jobs at Red Ventures</title></head><body>
  <h2 class="section-header">60 jobs</h2>
  <div class="job-posts--table"><table><tbody>
    ${boardRow(7727006, "Director, Account Management | Allconnect", "Charlotte, NC")}
    ${boardRow(8244939, "Director of Digital Growth | Allconnect", "Charlotte, NC")}
  </tbody></table></div></body></html>`;

describe("Greenhouse embed on a company's own careers page", () => {
  it("captures the single-job iframe as one job under its canonical URL", async () => {
    const page = loadContentScript({ url: JOB_APP_URL, html: JOB_APP_HTML });

    expect(await page.detectPage()).toMatchObject({ type: "greenhouseJob", jobCount: 1 });
    const { payload } = await page.build();

    expect(payload.jobs).toHaveLength(1);
    const [job] = payload.jobs;
    expect(job).toMatchObject({
      url: "https://job-boards.greenhouse.io/redventures/jobs/8250089",
      job_title: "Senior Software Engineer | Bankrate",
      company: "Red Ventures",
    });
    expect(job.listing_text).toContain("Location: Charlotte, NC");
    expect(job.listing_text).toContain("build our rate tables");
    expect(job.listing_text).not.toContain("Voluntary Demographic Questions");
  });

  it("captures the board iframe's gh_jid links under the same canonical URLs", async () => {
    const page = loadContentScript({ url: JOB_BOARD_URL, html: JOB_BOARD_HTML });

    expect(await page.detectPage()).toMatchObject({ type: "greenhouseSearch", jobCount: 2 });
    const { payload } = await page.build();

    expect(payload.jobs.map((job) => job.url)).toEqual([
      "https://job-boards.greenhouse.io/redventures/jobs/7727006",
      "https://job-boards.greenhouse.io/redventures/jobs/8244939",
    ]);
    // A browser's innerText puts the location on its own line, which the title
    // read drops; jsdom's layout-free stand-in runs the two <p>s together.
    expect(payload.jobs[0].job_title).toMatch(/^Director, Account Management \| Allconnect/);
  }, 10000);

  it("ignores gh_jid links outside the board embed", async () => {
    const page = loadContentScript({
      url: "https://job-boards.greenhouse.io/redventures",
      html: `<html><body>
        <a href="https://job-boards.greenhouse.io/redventures/jobs/111">Real posting</a>
        <a href="https://example.com/careers?gh_jid=222">Someone else's link</a>
      </body></html>`,
    });

    expect(await page.detectPage()).toMatchObject({ type: "greenhouseSearch", jobCount: 1 });
  });
});
