/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-02
// amazon.jobs job pages have no JobPosting JSON-LD, and their client-rendered
// RelatedJobs widget adds 5 recommendation links, so the generic link scan used to
// report 6 jobs for a single posting. Fixture markup mirrors
// amazon.jobs/en/jobs/10517525/sr-technical-account-manager-sr-tam as fetched that day,
// with the widget's links rendered in as they are in the live DOM.
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const URL_ = "https://amazon.jobs/en/jobs/10517525/sr-technical-account-manager-sr-tam";

const relatedJob = (id, title) =>
  `<li class="related-job"><a href="/en/jobs/${id}/related-slug"><h3>${title}</h3></a></li>`;

const PAGE_HTML = `<html><body>
  <div id="job-detail">
    <div class="info"><h1 class="title" title="Sr. Technical Account Manager, Sr. TAM">Sr. Technical Account Manager, Sr. TAM</h1>
      <div class="details-line"><p class="meta">Job ID: 10517525 | Amazon Web Services, Inc.</p></div>
    </div>
    <a id="apply-button" href="https://www.amazon.jobs/applicant/jobs/10517525/apply">Apply now</a>
  </div>
  <div class="container" id="job-detail-body"><div class="row">
    <div class="col-12 col-md-7"><div class="content">
      <div class="section description"><h2>Description</h2>AWS Applied AI Solutions (AAIS) is building toward a future.</div>
      <div class="section"><h2>Basic Qualifications</h2>- 5+ years of design/implementation/operations</div>
    </div></div>
    <div class="col-12 col-md-5"><div class="associations col-12">
      <div class="association location-icon"><div class="association-content"><ul><li>USA, WA, Seattle</li><li>USA, TX, Austin</li></ul></div></div>
    </div></div>
  </div></div>
  <div data-react-class="RelatedJobs"><ul>
    ${relatedJob(10400001, "Solutions Architect I")}
    ${relatedJob(10400002, "Solutions Architect II")}
    ${relatedJob(10400003, "Intern - Solutions Architect")}
    ${relatedJob(10400004, "Sr. Solutions Architect")}
    ${relatedJob(10400005, "Principal Solutions Architect")}
  </ul></div>
</body></html>`;

describe("amazon.jobs single job page", () => {
  it("detects one job despite the related-jobs widget's links", async () => {
    const page = await loadContentScript({ url: URL_, html: PAGE_HTML }).detectPage();
    expect(page).toMatchObject({ type: "amazonJob", jobCount: 1, site: "Amazon" });
  });

  it("captures only the posting, with the hiring entity, req ID and locations", async () => {
    const { payload } = await loadContentScript({ url: URL_, html: PAGE_HTML }).build();

    expect(payload.jobs).toHaveLength(1);
    const [job] = payload.jobs;
    expect(job).toMatchObject({
      url: URL_,
      job_title: "Sr. Technical Account Manager, Sr. TAM",
      company: "Amazon Web Services, Inc.",
    });
    expect(job.listing_text).toContain("Req ID: 10517525");
    expect(job.listing_text).toContain("USA, WA, Seattle");
    expect(job.listing_text).toContain("AWS Applied AI Solutions");
    expect(job.listing_text).not.toContain("Intern - Solutions Architect");
  });
});
