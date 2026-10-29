/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-29
// phenomSearchRecorder.js runs in the page's own JS world: it must record the
// page's latest Phenom search request for content.js and pass every request
// through untouched. Request shapes are the ones jobs.cvshealth.com sent live.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const SOURCE = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "phenomSearchRecorder.js"),
  "utf8",
);

const SEARCH_BODY = {
  ddoKey: "eagerLoadRefineSearch",
  keywords: "software",
  selected_fields: { remote: ["Remote"], type: ["Full time"] },
  locationData: { sliderRadius: 50, aboveMaxRadius: true, LocationUnit: "miles" },
  from: 0,
  size: 10,
};

function loadRecorder() {
  const { window } = new JSDOM("<html><body></body></html>", {
    url: "https://jobs.cvshealth.com/us/en/search-results?keywords=software",
    runScripts: "outside-only",
  });
  const pageFetch = vi.fn(async () => ({ ok: true }));
  window.fetch = pageFetch;
  // The page's own XHR, standing in for jsdom's (which would really send).
  const sentXhrBodies = [];
  window.XMLHttpRequest = class {
    open() {}
    setRequestHeader() {}
    send(body) { sentXhrBodies.push(body); }
  };
  window.eval(SOURCE);
  const recorded = () => JSON.parse(window.document.documentElement.getAttribute("data-jha-phenom-search"));
  return { window, pageFetch, sentXhrBodies, recorded };
}

describe("phenomSearchRecorder", () => {
  it("records the page's search request and passes it through unchanged", async () => {
    const { window, pageFetch, recorded } = loadRecorder();
    const init = {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrf-token": "live-token" },
      body: JSON.stringify(SEARCH_BODY),
    };

    await window.fetch("https://jobs.cvshealth.com/widgets", init);

    expect(pageFetch).toHaveBeenCalledWith("https://jobs.cvshealth.com/widgets", init);
    expect(recorded()).toEqual({ body: SEARCH_BODY, csrfToken: "live-token" });
  });

  it("ignores widgets calls that aren't a search", async () => {
    const { window, recorded } = loadRecorder();
    await window.fetch("/widgets", { method: "POST", body: JSON.stringify(SEARCH_BODY) });
    await window.fetch("/widgets", {
      method: "POST",
      body: JSON.stringify({ ddoKey: "getJobStatusListV2", jobSeqNos: ["X"] }),
    });
    await window.fetch("/analytics", { method: "POST", body: JSON.stringify({ ...SEARCH_BODY, keywords: "other" }) });

    expect(recorded().body.keywords).toBe("software");
  });

  it("records a search sent by XMLHttpRequest, token included", () => {
    const { window, sentXhrBodies, recorded } = loadRecorder();
    const body = JSON.stringify({ ...SEARCH_BODY, ddoKey: "refineSearch" });
    const xhr = new window.XMLHttpRequest();
    xhr.open("POST", "/widgets");
    xhr.setRequestHeader("X-CSRF-TOKEN", "xhr-token");
    xhr.send(body);

    expect(sentXhrBodies).toEqual([body]);
    expect(recorded()).toMatchObject({ csrfToken: "xhr-token", body: { ddoKey: "refineSearch" } });
  });
});
