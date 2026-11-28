/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-28
// Glassdoor Recruiter's "matches" page (glassdoor.com/recruiter/matches)
// shows one job match at a time with a "N of TOTAL" counter and
// Next/Previous buttons that swap the detail pane's content in place --
// confirmed live: no per-job URL anywhere on the page (Apply is a plain
// <button>), and the two duplicate "N of TOTAL" spans findNextPageControl-
// style text matching would have been fooled by (one is a mid-transition
// stale copy) are why this needs its own
// [data-testid="match-navigation-progress"] reader rather than the generic
// smartGeneric path. Fixture mirrors the real data-testid names as inspected
// live that day.
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const URL = "https://www.glassdoor.com/recruiter/matches";

const MATCHES = [
  {
    title: "Senior Software Engineer",
    company: "GitHub",
    location: "Remote",
    salary: "$128K – $339K/yr (Glassdoor est.)",
    posted: "Posted 1 week ago",
    description: "About GitHub: the world's leading platform for agentic software development.",
  },
  {
    title: "Staff Software Engineer",
    company: "ASG",
    location: "Remote",
    salary: "$200K – $220K/yr (Glassdoor est.)",
    posted: "Posted 4 weeks ago",
    description: "ASG is an unconventional group of market-leading SaaS software companies.",
  },
  {
    title: "Senior Software Engineer",
    company: "Lockheed Martin",
    location: "Remote",
    salary: "$109K – $280K/yr (Glassdoor est.)",
    posted: "Posted 2 weeks ago",
    description: "WHAT WE'RE DOING: come join the EADGE-T team.",
  },
];

function matchHtml(index) {
  const m = MATCHES[index - 1];
  return `
    <span data-testid="match-navigation-previous">&lt;</span>
    <span data-testid="match-navigation-progress">${index} of ${MATCHES.length}</span>
    <button type="button" data-testid="match-navigation-next">&gt;</button>
    <h1 data-testid="super-match-job-title">${m.title}</h1>
    <div data-testid="super-match-employer-name">${m.company}</div>
    <div data-testid="job-location">${m.location}</div>
    <div data-testid="detailSalary">${m.salary}</div>
    <div data-testid="super-match-listing-age">${m.posted}</div>
    <div data-testid="job-description-content">${m.description}</div>
    <button type="button" data-testid="super-match-pass-button">Pass</button>
    <button type="button" data-testid="save-job-button">Save</button>
    <button type="button" data-testid="apply-button">Apply</button>
  `;
}

function loadGlassdoorPage() {
  const page = loadContentScript({ url: URL, html: matchHtml(1) });
  const { document } = page.window;
  let current = 1;

  function render() {
    document.body.innerHTML = matchHtml(current);
    wireNext();
  }
  function wireNext() {
    const next = document.querySelector('[data-testid="match-navigation-next"]');
    if (current >= MATCHES.length) {
      next.disabled = true;
      return;
    }
    next.addEventListener("click", () => {
      current += 1;
      render();
    });
  }
  wireNext();

  return page;
}

describe("Glassdoor Recruiter matches (one match at a time, no per-job URL)", () => {
  it("reports the total from the N of TOTAL counter", async () => {
    const page = loadGlassdoorPage();

    expect(await page.detectPage()).toMatchObject({
      site: "Glassdoor",
      type: "glassdoorMatches",
      jobCount: 3,
      captureModes: ["detail", "list"],
    });
  });

  it("steps through every match via Next, never clicking Pass/Save/Apply", async () => {
    const page = loadGlassdoorPage();
    const { document } = page.window;
    let passClicked = false, saveClicked = false, applyClicked = false;
    // Re-wired on every render() -- attach via event delegation on body instead.
    document.body.addEventListener("click", (e) => {
      const testid = e.target.getAttribute?.("data-testid");
      if (testid === "super-match-pass-button") passClicked = true;
      if (testid === "save-job-button") saveClicked = true;
      if (testid === "apply-button") applyClicked = true;
    });

    const { payload } = await page.build({ captureMode: "list" });

    expect(payload.jobs.map((j) => j.job_title)).toEqual([
      "Senior Software Engineer",
      "Staff Software Engineer",
      "Senior Software Engineer",
    ]);
    expect(payload.jobs.map((j) => j.company)).toEqual(["GitHub", "ASG", "Lockheed Martin"]);
    expect(payload.jobs[2].listing_text).toContain("EADGE-T team");
    // The two "Senior Software Engineer" matches (GitHub and Lockheed Martin)
    // must not collapse into one job -- the synthetic url has to disambiguate them.
    expect(new Set(payload.jobs.map((j) => j.url)).size).toBe(3);
    expect(passClicked).toBe(false);
    expect(saveClicked).toBe(false);
    expect(applyClicked).toBe(false);
  });

  it("captures only the current match in detail mode", async () => {
    const page = loadGlassdoorPage();

    const { payload } = await page.build({ captureMode: "detail" });

    expect(payload.jobs).toHaveLength(1);
    expect(payload.jobs[0].job_title).toBe("Senior Software Engineer");
    expect(payload.jobs[0].company).toBe("GitHub");
  });
});
