/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// The popup on a LinkedIn conversation: "Capture this conversation" instead of
// the job capture, and what the app did with it, in words.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const POPUP_HTML = fs.readFileSync(path.join(DIR, "popup.html"), "utf8");
const POPUP_JS = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");
const LINKEDIN_THREAD_JS = fs.readFileSync(path.join(DIR, "linkedin-thread.js"), "utf8");

function openPopup(url, captureResponse) {
  const { window } = new JSDOM(POPUP_HTML, { url: "chrome-extension://test/popup.html", runScripts: "outside-only" });
  const sendMessage = vi.fn(async (message) => {
    if (message.action === "getStashedAnswers") return [];
    if (message.action === "captureLinkedInConversation") return captureResponse;
    return undefined;
  });
  const executeScript = vi.fn(async () => []);
  window.chrome = {
    runtime: {
      onMessage: { addListener: () => {} },
      getManifest: () => ({ version: "test" }),
      openOptionsPage: () => {},
      sendMessage,
      lastError: undefined,
    },
    storage: { sync: { get: async (defaults) => ({ ...defaults }), set: async () => {} } },
    tabs: { query: async () => [{ id: 3, url }], sendMessage: () => {} },
    scripting: { executeScript },
  };
  window.eval(LINKEDIN_THREAD_JS);
  window.eval(POPUP_JS);
  if (window.document.readyState !== "loading") {
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  }
  const $ = (id) => window.document.getElementById(id);
  return { $, sendMessage, executeScript };
}

const THREAD_URL = "https://www.linkedin.com/messaging/thread/2-UmVjcnVpdGVy/";

describe("Capture this conversation", () => {
  it("replaces the job capture on a LinkedIn thread and reports what was saved", async () => {
    const { $, sendMessage, executeScript } = openPopup(THREAD_URL, {
      ok: true, suggestion_id: 4, status: "suggested", added: true,
      counterpart_name: "Rita Recruiter", messages: 3, messages_logged: 0,
    });
    await vi.waitFor(() => expect($("capture-conversation-btn").style.display).toBe("flex"));
    expect($("send-btn").style.display).toBe("none");
    expect($("site-name").textContent).toBe("LinkedIn conversation");
    expect(executeScript).not.toHaveBeenCalled(); // no job or form probing on a thread

    $("capture-conversation-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("✓ Saved 3 messages with Rita Recruiter to Leads › Suggested."));
    expect(sendMessage).toHaveBeenCalledWith({ action: "captureLinkedInConversation", tabId: 3 });
    expect($("status-row").className).toBe("status-row success");
    expect($("capture-conversation-btn").disabled).toBe(false);
  });

  it.each([
    [{ status: "suggested", added: false, messages: 4, messages_logged: 0 }, "✓ Updated Rita Recruiter's suggestion in Leads › Suggested (4 messages)."],
    [{ status: "accepted", added: false, messages: 4, messages_logged: 1 }, "✓ 1 new message added to Rita Recruiter's timeline."],
    [{ status: "accepted", added: false, messages: 4, messages_logged: 0 }, "✓ Rita Recruiter's timeline already has these 4 messages."],
  ])("says what happened to a person already known (%o)", async (outcome, text) => {
    const { $ } = openPopup(THREAD_URL, { ok: true, suggestion_id: 4, counterpart_name: "Rita Recruiter", ...outcome });
    await vi.waitFor(() => expect($("capture-conversation-btn").style.display).toBe("flex"));

    $("capture-conversation-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe(text));
  });

  it("shows the reason a capture failed", async () => {
    const { $ } = openPopup(THREAD_URL, { ok: false, error: "Reload this conversation, then capture again." });
    await vi.waitFor(() => expect($("capture-conversation-btn").style.display).toBe("flex"));

    $("capture-conversation-btn").click();

    await vi.waitFor(() => expect($("status-row").textContent).toBe("Reload this conversation, then capture again."));
    expect($("status-row").className).toBe("status-row error");
  });

  it("isn't offered anywhere else on LinkedIn", async () => {
    const { $, executeScript } = openPopup("https://www.linkedin.com/jobs/view/123/", undefined);
    await vi.waitFor(() => expect(executeScript).toHaveBeenCalled());
    expect($("capture-conversation-btn").style.display).toBe("none");
  });
});
