/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// The popup's "Answers you didn't save" list: answers autofill.js stashed
// when an application was submitted before they were saved.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const POPUP_HTML = fs.readFileSync(path.join(DIR, "popup.html"), "utf8");
const POPUP_JS = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");
// ai: claude-opus-5-5 | 2026-11-18 -- popup.html loads it before popup.js
const LINKEDIN_THREAD_JS = fs.readFileSync(path.join(DIR, "linkedin-thread.js"), "utf8");

const STASHED = {
  pageUrl: "https://jobs.lever.co/acme/1/apply",
  pageTitle: "Acme - Backend Engineer",
  platform: "Lever",
  answers: [
    { question_text: "What is your notice period?", answer_text: "2 weeks" },
    { question_text: "Willing to work onsite?", answer_text: "No" },
  ],
};

function openPopup({ learnResponse = { ok: true } } = {}) {
  const { window } = new JSDOM(POPUP_HTML, { url: "chrome-extension://test/popup.html", runScripts: "outside-only" });
  let stashed = [STASHED];
  const sendMessage = vi.fn(async (message) => {
    if (message.action === "getStashedAnswers") return stashed;
    if (message.action === "clearStashedAnswers") {
      stashed = stashed.filter((entry) => entry.pageUrl !== message.pageUrl);
      return { ok: true };
    }
    if (message.action === "learnAnswers") return learnResponse;
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
    tabs: { query: async () => [{ id: 1 }], sendMessage: () => {} },
    scripting: { executeScript: async () => [] },
  };
  window.eval(LINKEDIN_THREAD_JS);
  window.eval(POPUP_JS);
  if (window.document.readyState !== "loading") {
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  }
  const container = window.document.getElementById("stashed-answers");
  return { window, container, sendMessage };
}

const buttonNamed = (container, text) => [...container.querySelectorAll("button")].find((b) => b.textContent === text);

describe("stashed answers in the popup", () => {
  it("lists them and saves only the ticked ones", async () => {
    const { container, sendMessage } = openPopup();
    await vi.waitFor(() => expect(container.querySelectorAll(".stashed-row")).toHaveLength(2));
    expect(container.style.display).toBe("block");
    expect(container.querySelector(".stashed-page").textContent).toBe("Acme - Backend Engineer");

    container.querySelectorAll('.stashed-row input[type="checkbox"]')[1].checked = false;
    buttonNamed(container, "Save selected").click();

    await vi.waitFor(() => expect(container.style.display).toBe("none"));
    expect(sendMessage).toHaveBeenCalledWith({
      action: "learnAnswers",
      answers: [STASHED.answers[0]],
      platform: "Lever",
    });
  });

  it("keeps them listed with the error when saving fails", async () => {
    const { container } = openPopup({ learnResponse: { ok: false, error: "Is Job Hunt App running?" } });
    await vi.waitFor(() => expect(container.querySelectorAll(".stashed-row")).toHaveLength(2));

    buttonNamed(container, "Save selected").click();

    await vi.waitFor(() => expect(container.querySelector(".stashed-error").textContent).toBe("Is Job Hunt App running?"));
    expect(container.querySelectorAll(".stashed-row")).toHaveLength(2);
  });

  it("discards them without saving", async () => {
    const { container, sendMessage } = openPopup();
    await vi.waitFor(() => expect(container.querySelectorAll(".stashed-row")).toHaveLength(2));

    buttonNamed(container, "Discard").click();

    await vi.waitFor(() => expect(container.style.display).toBe("none"));
    expect(sendMessage.mock.calls.some(([m]) => m.action === "learnAnswers")).toBe(false);
  });
});
