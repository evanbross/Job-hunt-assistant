/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-25
// Regression coverage for Getro-powered boards (e.g. jobs.insightpartners.com,
// 2026-11-25): the page serves one explicit "Load more" click, then removes
// that control for good and switches to scroll-triggered loading -- confirmed
// live: 20 -> 39 of 89 jobs on the click, then 39 -> 88 across repeated
// scrolls with the button never reappearing. buildSmartGenericPayload's
// click-only loop used to treat "no control found" as "no more pages" and
// stopped at 39, silently looking complete. Fixture mirrors that shape: one
// click adds a job and removes the button, then scrolling keeps adding jobs
// for a couple of rounds before genuinely running out.
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const URL = "https://jobs.examplevc.com/jobs?q=engineer";

const HTML = `<html><body>
  <div id="list">
    <a href="/companies/acme/jobs/100-senior-engineer">Senior Engineer</a>
    <a href="/companies/acme/jobs/101-staff-engineer">Staff Engineer</a>
  </div>
  <button id="load-more" type="button">Load more</button>
</body></html>`;

describe("Getro-style board that switches from a Load-more click to scroll-triggered loading", () => {
  it("keeps capturing jobs after the Load-more control disappears", async () => {
    const page = loadContentScript({ url: URL, html: HTML });
    const { document, window } = page.window;

    const loadMoreBtn = document.getElementById("load-more");
    // jsdom has no layout, so give the button the size a rendered one has.
    loadMoreBtn.getBoundingClientRect = () => ({ width: 80, height: 30, top: 0, left: 0, right: 80, bottom: 30 });
    loadMoreBtn.addEventListener("click", () => {
      document.getElementById("list").insertAdjacentHTML(
        "beforeend",
        '<a href="/companies/acme/jobs/102-clicked-job">Clicked Job</a>',
      );
      loadMoreBtn.remove(); // the site's own behavior: gone for good after one use
    });

    let scrollCalls = 0;
    window.scrollTo = () => {
      scrollCalls += 1;
      if (scrollCalls === 1) {
        document.getElementById("list").insertAdjacentHTML(
          "beforeend",
          '<a href="/companies/acme/jobs/103-scrolled-one">Scrolled One</a>',
        );
      } else if (scrollCalls === 2) {
        document.getElementById("list").insertAdjacentHTML(
          "beforeend",
          '<a href="/companies/acme/jobs/104-scrolled-two">Scrolled Two</a>',
        );
      }
      // Further calls add nothing, like the real board once it truly runs out.
    };

    expect(await page.detectPage()).toMatchObject({ type: "smartGeneric", jobCount: 2 });
    const { payload } = await page.build();

    expect(payload.jobs.map((job) => job.url.split("/").pop())).toEqual([
      "100-senior-engineer",
      "101-staff-engineer",
      "102-clicked-job",
      "103-scrolled-one",
      "104-scrolled-two",
    ]);
    expect(scrollCalls).toBeGreaterThanOrEqual(3); // stalled out, not cut off early
  }, 15000);
});
