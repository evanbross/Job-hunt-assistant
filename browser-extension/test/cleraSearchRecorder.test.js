/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-02
// cleraSearchRecorder.js runs in the page's own JS world: it must record the
// page's latest job search (minus its paging cursor) for content.js and pass
// every request through untouched. The body is the shape getclera.com sent live.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const SOURCE = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "cleraSearchRecorder.js"),
  "utf8",
);

const SEARCH = {
  filters: {
    countries: ["United States"],
    workArrangement: ["remote"],
    experienceLevel: ["5-10", "10+"],
    salaryFloor: 150000,
  },
  sort: "relevance",
  limit: 25,
  scopeToTalentRoles: false,
};

function loadRecorder() {
  const { window } = new JSDOM("<html><body></body></html>", {
    url: "https://www.getclera.com/dashboard/jobs?experience=senior",
    runScripts: "outside-only",
  });
  const pageFetch = vi.fn(async () => ({ ok: true }));
  window.fetch = pageFetch;
  window.eval(SOURCE);
  const recorded = () => JSON.parse(window.document.documentElement.getAttribute("data-jha-clera-search"));
  return { window, pageFetch, recorded };
}

describe("cleraSearchRecorder", () => {
  it("records the page's search without its cursor and passes the request through unchanged", async () => {
    const { window, pageFetch, recorded } = loadRecorder();
    const init = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...SEARCH, cursor: "AQNACAAAAAAAAANAOQ" }),
    };

    await window.fetch("/api/talents/me/external-jobs", init);

    expect(pageFetch).toHaveBeenCalledWith("/api/talents/me/external-jobs", init);
    expect(recorded()).toEqual(SEARCH);
  });

  it("ignores other requests, including the facets call that sends the same filters", async () => {
    const { window, recorded } = loadRecorder();
    const body = JSON.stringify(SEARCH);

    await window.fetch("/api/jobs/external/facets", { method: "POST", body });
    await window.fetch("/api/talents/me/external-jobs"); // the GET the page also makes
    await window.fetch("/api/talents/me/external-jobs", { method: "POST", body: "not json" });

    expect(recorded()).toBeNull();
  });
});
