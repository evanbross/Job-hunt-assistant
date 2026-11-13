/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-02
// The popup's "Capture the first N of M jobs" box for list captures that can
// stop early (Clera first). Loads the real popup.html + popup.js with a minimal
// chrome.* mock whose page scan answers with a Clera search result.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const POPUP_HTML = fs.readFileSync(path.join(DIR, "popup.html"), "utf8");
const POPUP_JS = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");

const CLERA_SCAN = {
  site: "Clera",
  type: "cleraSplit",
  jobCount: 566,
  description: "566 jobs found",
  captureModes: ["list", "detail"],
  secondsPerJob: 0.7,
};

async function openPopup({ stored = {} } = {}) {
  const { window } = new JSDOM(POPUP_HTML, { url: "chrome-extension://test/popup.html", runScripts: "outside-only" });
  const storage = { ...stored };
  const sentMessages = [];
  window.chrome = {
    runtime: {
      onMessage: { addListener: () => {} },
      getManifest: () => ({ version: "test" }),
      openOptionsPage: () => {},
      sendMessage: vi.fn(),
      lastError: undefined,
    },
    storage: {
      sync: {
        get: async (defaults) => ({ ...defaults, ...storage }),
        set: async (values) => Object.assign(storage, values),
      },
    },
    tabs: {
      query: async () => [{ id: 1 }],
      sendMessage: (tabId, message, options, callback) => {
        sentMessages.push(message);
        callback({ error: "stopped by test" });
      },
    },
    scripting: {
      executeScript: async ({ func }) =>
        func && String(func).includes("__jhaDetectPage") ? [{ frameId: 0, result: CLERA_SCAN }] : [],
    },
  };
  window.eval(POPUP_JS);
  // jsdom fires DOMContentLoaded itself once parsing finishes; firing it again
  // would run the popup's startup twice (double listeners), which Chrome never does.
  if (window.document.readyState !== "loading") {
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  }
  const $ = (id) => window.document.getElementById(id);
  await vi.waitFor(() => expect($("site-name").textContent).toBe("Clera"));
  return { window, $, storage, sentMessages };
}

function setMaxJobs(ctx, value) {
  const input = ctx.$("max-jobs-input");
  input.value = value;
  input.dispatchEvent(new ctx.window.Event("change"));
}

describe("popup max-jobs box", () => {
  it("offers the first 50 by default in list mode, and hides in detail mode", async () => {
    const ctx = await openPopup();

    expect(ctx.$("max-jobs-options").style.display).toBe("block");
    expect(ctx.$("max-jobs-input").value).toBe("50");
    expect(ctx.$("max-jobs-of").textContent).toBe("of 566 jobs (~1 min)");
    expect(ctx.$("send-btn").textContent).toBe("Send 50 Jobs to App");

    ctx.$("capture-mode-detail").click();
    expect(ctx.$("max-jobs-options").style.display).toBe("none");
    expect(ctx.$("send-btn").textContent).toBe("Send 1 Job to App");
  });

  it("remembers the typed number but never asks for more than the search has", async () => {
    const ctx = await openPopup({ stored: { cleraMaxJobs: 120 } });
    expect(ctx.$("send-btn").textContent).toBe("Send 120 Jobs to App");

    setMaxJobs(ctx, "1000");

    expect(ctx.storage.cleraMaxJobs).toBe(1000);
    expect(ctx.$("max-jobs-input").value).toBe("566");
    expect(ctx.$("max-jobs-of").textContent).toBe("of 566 jobs (~7 min)");
    expect(ctx.$("send-btn").textContent).toBe("Send 566 Jobs to App");
  });

  it("sends the chosen limit with the capture request", async () => {
    const ctx = await openPopup();
    setMaxJobs(ctx, "75");

    ctx.$("send-btn").click();
    await vi.waitFor(() => expect(ctx.sentMessages).toHaveLength(1));

    expect(ctx.sentMessages[0]).toMatchObject({ action: "build", options: { captureMode: "list", maxJobs: 75 } });
  });
});
