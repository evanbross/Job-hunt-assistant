/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-28
// The popup's "Add Sender as Email Scan Source" button: offered instead of
// the job capture on an open Gmail message (content.js's gmailMessage type),
// pre-filling the sender address content.js found but letting the user edit
// it before submitting.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const POPUP_HTML = fs.readFileSync(path.join(DIR, "popup.html"), "utf8");
const POPUP_JS = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");
const LINKEDIN_THREAD_JS = fs.readFileSync(path.join(DIR, "linkedin-thread.js"), "utf8");

const GMAIL_SCAN = {
  site: "Gmail",
  type: "gmailMessage",
  jobCount: 0,
  description: "Email from Greenhouse Notifications",
  senderEmail: "notifications@greenhouse.io",
  senderName: "Greenhouse Notifications",
  subject: "New jobs matching your search",
};
const GENERIC_SCAN = { site: "example.com", type: "html", jobCount: 0, description: "", captureModes: [] };

async function openPopup({ scanResult = GMAIL_SCAN, addEmailSourceResponse } = {}) {
  const { window } = new JSDOM(POPUP_HTML, { url: "chrome-extension://test/popup.html", runScripts: "outside-only" });
  const sendMessage = vi.fn(async (message) => {
    if (message.action === "getStashedAnswers") return [];
    if (message.action === "addEmailSource") return addEmailSourceResponse;
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
    tabs: { query: async () => [{ id: 3, url: "https://mail.google.com/mail/u/0/#inbox/abc123" }], sendMessage: () => {} },
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
  await vi.waitFor(() => expect($("site-name").textContent).toBe(scanResult.site));
  return { $, sendMessage };
}

describe("Add Sender as Email Scan Source", () => {
  it("is offered on an open Gmail message, pre-filled with the detected sender", async () => {
    const { $ } = await openPopup({
      addEmailSourceResponse: { ok: true, source: { name: "Greenhouse Notifications" }, already_added: false },
    });

    expect($("add-email-source-btn").style.display).toBe("flex");
    expect($("email-source-input").value).toBe("notifications@greenhouse.io");
    expect($("send-btn").disabled).toBe(true);
    expect($("add-career-site-btn").style.display).toBe("none");
  });

  it("submits the (possibly edited) address and name, and reports what was added", async () => {
    const { $, sendMessage } = await openPopup({
      addEmailSourceResponse: { ok: true, source: { name: "Greenhouse Notifications" }, already_added: false },
    });
    $("email-source-input").value = "jobs@greenhouse.io";

    $("add-email-source-btn").click();

    await vi.waitFor(() =>
      expect($("status-row").textContent).toBe('✓ Added "Greenhouse Notifications" as an email scan source.')
    );
    expect(sendMessage).toHaveBeenCalledWith({
      action: "addEmailSource",
      name: "Greenhouse Notifications",
      email: "jobs@greenhouse.io",
    });
    expect($("status-row").className).toBe("status-row success");
  });

  it("says when the sender was already tracked", async () => {
    const { $ } = await openPopup({
      addEmailSourceResponse: { ok: true, source: { name: "Greenhouse" }, already_added: true },
    });

    $("add-email-source-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("Already tracked — nothing to add."));
  });

  it("refuses to submit a blank address", async () => {
    const { $, sendMessage } = await openPopup();
    $("email-source-input").value = "   ";

    $("add-email-source-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("Enter a sender email address first."));
    expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ action: "addEmailSource" }));
  });

  it("is hidden on an ordinary job page", async () => {
    const { $ } = await openPopup({ scanResult: GENERIC_SCAN });
    expect($("add-email-source-btn").style.display).toBe("none");
  });
});
