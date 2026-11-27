/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// The popup's "Add as Job Board to Scan" button: works on any job-board page
// (not just ones detectPage() specifically recognizes), and is hidden on a
// known non-job page (content.js's isBlockedHost) or a LinkedIn conversation.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const POPUP_HTML = fs.readFileSync(path.join(DIR, "popup.html"), "utf8");
const POPUP_JS = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");
const LINKEDIN_THREAD_JS = fs.readFileSync(path.join(DIR, "linkedin-thread.js"), "utf8");

// A page with no bespoke ATS parser: the generic "Send Page to App" fallback
// (see resetButton's comment), not a GENERIC_PAGE_TYPES "nothing found" null.
const GENERIC_SCAN = { site: "example.com", type: "html", jobCount: 0, description: "", captureModes: [] };
const BLOCKED_SCAN = { site: "mail.google.com", type: "blocked", jobCount: 0, captureModes: [] };

async function openPopup({ scanResult = GENERIC_SCAN, tabUrl = "https://example.com/jobs?remote=true", addCareerSiteResponse } = {}) {
  const { window } = new JSDOM(POPUP_HTML, { url: "chrome-extension://test/popup.html", runScripts: "outside-only" });
  const sendMessage = vi.fn(async (message) => {
    if (message.action === "getStashedAnswers") return [];
    if (message.action === "addCareerSite") return addCareerSiteResponse;
    return undefined;
  });
  window.chrome = {
    runtime: {
      onMessage: { addListener: () => {} },
      getManifest: () => ({ version: "test" }),
      openOptionsPage: () => {},
      sendMessage,
      lastError: undefined,
    },
    storage: { sync: { get: async (defaults) => ({ ...defaults }), set: async () => {} } },
    tabs: { query: async () => [{ id: 3, url: tabUrl }], sendMessage: () => {} },
    scripting: {
      executeScript: async ({ func }) => {
        if (func && String(func).includes("__jhaDetectPage")) return [{ frameId: 0, result: scanResult }];
        return [];
      },
    },
  };
  window.eval(LINKEDIN_THREAD_JS);
  window.eval(POPUP_JS);
  if (window.document.readyState !== "loading") {
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  }
  const $ = (id) => window.document.getElementById(id);
  await vi.waitFor(() =>
    expect($("site-name").textContent === scanResult.site || $("capture-conversation-btn").style.display === "flex").toBe(true)
  );
  return { $, sendMessage };
}

describe("Add as Job Board to Scan", () => {
  it("is offered on a page with no bespoke ATS parser and reports what was added", async () => {
    const { $, sendMessage } = await openPopup({
      addCareerSiteResponse: { ok: true, source: { id: 9, name: "Remote Jobs | Example" }, already_added: false },
    });
    expect($("add-career-site-btn").style.display).not.toBe("none");

    $("add-career-site-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe('✓ Added "Remote Jobs | Example" as a job board to scan.'));
    expect(sendMessage).toHaveBeenCalledWith({ action: "addCareerSite", tabId: 3 });
    expect($("status-row").className).toBe("status-row success");
    expect($("add-career-site-btn").disabled).toBe(false);
    expect($("add-career-site-btn").textContent).toBe("Add as Job Board to Scan");
  });

  it("says when the search was already tracked", async () => {
    const { $ } = await openPopup({
      addCareerSiteResponse: { ok: true, source: { id: 9, name: "Example" }, already_added: true },
    });

    $("add-career-site-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("Already tracked — nothing to add."));
  });

  it("shows the reason adding it failed", async () => {
    const { $ } = await openPopup({
      addCareerSiteResponse: { ok: false, error: "Could not add this job board." },
    });

    $("add-career-site-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("Could not add this job board."));
    expect($("status-row").className).toBe("status-row error");
  });

  it("is hidden on a known non-job page", async () => {
    const { $ } = await openPopup({ scanResult: BLOCKED_SCAN });
    expect($("add-career-site-btn").style.display).toBe("none");
  });

  it("is hidden on a LinkedIn conversation", async () => {
    const { $ } = await openPopup({ tabUrl: "https://www.linkedin.com/messaging/thread/2-UmVjcnVpdGVy/" });
    await vi.waitFor(() => expect($("capture-conversation-btn").style.display).toBe("flex"));
    expect($("add-career-site-btn").style.display).toBe("none");
  });
});
