/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */
// ai: claude-opus-5-5 | 2026-11-13
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const URL_ = "https://www.workatastartup.com/jobs/r/software-engineer";

// The company link text, byte-for-byte as stored on job 7665: NBSP before the
// batch, a bullet straight after it, then the company tagline.
const card = (id, company, slug, title) => `<div class="job-card">
  <a href="/companies/${slug}">${company}</a>
  <a href="/jobs/${id}">${title}</a>
  <p>Remote (US) | Full-time | fulltime | Software engineering</p>
</div>`;

const PAGE_HTML = `<html><body>
  ${card(98323, "Nitrode (W25)•Frontier AI research to advance game development", "nitrode", "Full-Stack Engineer")}
  ${card(99318, "PostHog", "posthog", "Site Reliability Engineer")}
</body></html>`;

describe("Work at a Startup listing", () => {
  it("captures the company name without its YC batch or tagline", async () => {
    const { payload } = await loadContentScript({ url: URL_, html: PAGE_HTML }).build();

    expect(payload.jobs.map((job) => [job.url, job.company, job.job_title])).toEqual([
      ["https://www.workatastartup.com/jobs/98323", "Nitrode", "Full-Stack Engineer"],
      ["https://www.workatastartup.com/jobs/99318", "PostHog", "Site Reliability Engineer"],
    ]);
  });
});
