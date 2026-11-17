/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
// Loads the REAL content.js into a fresh jsdom window per call, the way
// Chrome injects it into a tab. content.js is a classic script wrapped in an
// IIFE, so none of its functions are reachable from outside -- tests drive it
// through the same two entry points popup.js uses instead:
// window.__jhaDetectPage() (the allFrames probe) and the 'build' runtime
// message (the actual capture). That keeps the tests on the real
// popup↔content contract rather than on internals exposed just for testing.
//
// A JSDOM instance is built explicitly here rather than switching vitest's
// environment to "jsdom": content.js branches on location.hostname/pathname
// for nearly every site it supports, so each test needs its own URL, which a
// shared per-file environment can't give it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { vi } from "vitest";

const EXT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONTENT_SOURCE = fs.readFileSync(path.join(EXT_DIR, "content.js"), "utf8");
const AUTOFILL_SOURCE = fs.readFileSync(path.join(EXT_DIR, "autofill.js"), "utf8");
const MANIFEST_VERSION = JSON.parse(fs.readFileSync(path.join(EXT_DIR, "manifest.json"), "utf8")).version;

// A minimal fetch Response stand-in -- content.js only ever reads ok/status
// and calls text() or json() on what fetch resolves to.
export function htmlResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, text: async () => body, json: async () => JSON.parse(body) };
}

// options.iframes maps an iframe element id in `html` to the body HTML of its
// (same-origin, about:blank) document -- enough to model a wrapper page like
// iCIMS's #icims_content_iframe, which the top frame reads directly.
export function loadContentScript({ url, html, iframes = {}, fetchMock = vi.fn() }) {
  const { window, listeners, sentMessages } = createPageWindow({ url, html });

  for (const [id, bodyHtml] of Object.entries(iframes)) {
    const frame = window.document.getElementById(id);
    if (!frame) throw new Error(`loadContentScript: no iframe #${id} in html`);
    frame.contentDocument.body.innerHTML = bodyHtml;
  }
  window.fetch = fetchMock;

  window.eval(CONTENT_SOURCE);

  return {
    window,
    fetchMock,
    sentMessages,
    detectPage: () => window.__jhaDetectPage(),
    // Same message popup.js sends to capture a page; resolves with the
    // {payload, warning} the content script responds with, payload parsed.
    async build(options = {}) {
      const response = await new Promise((resolve) => {
        for (const listener of listeners) listener({ action: "build", options }, {}, resolve);
      });
      if (response.error) throw new Error(response.error);
      return { ...response, payload: response.payload ? JSON.parse(response.payload) : null };
    },
  };
}

// ai: claude-opus-5-5 | 2026-11-17
// Same idea for autofill.js, driven through the 'runAutofill' message that
// background.js sends after the popup's "Fill Application" click.
// respondTo(message) supplies runtime.sendMessage's reply (e.g. the
// background's answer to 'learnAnswers').
export function loadAutofillScript({ url, html, respondTo = () => null }) {
  const { window, listeners, sentMessages } = createPageWindow({ url, html, respondTo });
  window.eval(AUTOFILL_SOURCE);
  return {
    window,
    document: window.document,
    sentMessages,
    async runAutofill(data) {
      const response = await new Promise((resolve) => {
        for (const listener of listeners) listener({ action: "runAutofill", data }, {}, resolve);
      });
      if (!response.ok) throw new Error(response.error);
      return response.results;
    },
  };
}

function createPageWindow({ url, html, respondTo = () => null }) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;

  // jsdom has no layout engine, so it doesn't implement innerText at all
  // (it's undefined, not ""). content.js leans on innerText everywhere;
  // textContent is the closest stand-in a layout-free DOM can offer.
  if (!("innerText" in window.HTMLElement.prototype)) {
    Object.defineProperty(window.HTMLElement.prototype, "innerText", {
      get() { return this.textContent; },
      configurable: true,
    });
  }

  const listeners = [];
  const sentMessages = [];
  window.chrome = {
    runtime: {
      onMessage: { addListener: (fn) => listeners.push(fn) },
      sendMessage: vi.fn((message) => {
        sentMessages.push(message);
        return Promise.resolve(respondTo(message));
      }),
      getManifest: () => ({ version: MANIFEST_VERSION }),
    },
  };
  return { window, listeners, sentMessages };
}
