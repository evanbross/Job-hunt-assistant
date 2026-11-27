/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// background.js reporting why a queued capture never completed, instead of
// leaving the row for the server's slower staleness sweep to rediscover with
// no real reason attached. Covers both give-up paths: a same-tick redirect to
// a foreign domain (chrome.tabs.onUpdated's early check) and a capture that
// never answers at all (the 1.5-minute timeout alarm -> _abandonQueueTab).
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

const TAB_ID = 55;

function captureFailedCall(fetchMock) {
  return fetchMock.mock.calls.find(([url]) => String(url).includes("/capture-failed"));
}

describe("reporting a redirect to a foreign domain", () => {
  async function openQueuedTab(env, ctx, item) {
    env.fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [item] }) });
    ctx.chrome.tabs.create.mockResolvedValueOnce({ id: TAB_ID });
    await ctx.globals.pollQueue();
  }

  it("reports and closes the tab for an automatic_browser career-site capture", async () => {
    const env = new MockChromeEnvironment();
    const ctx = await env.loadBackgroundScript();
    await openQueuedTab(env, ctx, {
      url: "https://www.linkedin.com/jobs/search-results/?start=250",
      purpose: "import",
      source_id: 12,
    });
    env.fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) });

    ctx.fireTabsUpdated(TAB_ID, { status: "complete" }, { url: "https://accounts.example-sso.com/challenge" });
    await flushAsync();

    const call = captureFailedCall(env.fetchMock);
    expect(call).toBeTruthy();
    const body = JSON.parse(call[1].body);
    expect(body.needs_login).toBe(true);
    expect(body.queue_url).toBe("https://www.linkedin.com/jobs/search-results/?start=250");
    expect(ctx.chrome.tabs.remove).toHaveBeenCalledWith(TAB_ID);
    // Never got far enough to inject content.js -- this was never a real page load.
    expect(ctx.chrome.scripting.executeScript).not.toHaveBeenCalled();
  });

  it("leaves the tab open for a manual/email-triggered capture (no source_id)", async () => {
    const env = new MockChromeEnvironment();
    const ctx = await env.loadBackgroundScript();
    await openQueuedTab(env, ctx, {
      url: "https://jobs.example.com/posting/42",
      purpose: "import",
      source_id: null,
    });

    // A genuinely foreign-domain redirect -- e.g. an SSO login provider.
    ctx.fireTabsUpdated(TAB_ID, { status: "complete" }, { url: "https://accounts.example-sso.com/login" });
    await flushAsync();

    expect(captureFailedCall(env.fetchMock)).toBeFalsy();
    expect(ctx.chrome.tabs.remove).not.toHaveBeenCalled();
  });
});

describe("reporting a capture that never answers", () => {
  it("detects a login wall from the tab's url when it finally gives up", async () => {
    const env = new MockChromeEnvironment();
    const ctx = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({
        items: [{ url: "https://www.linkedin.com/jobs/search-results/", purpose: "import", source_id: 12 }],
      }),
    });
    ctx.chrome.tabs.create.mockResolvedValueOnce({ id: TAB_ID });
    await ctx.globals.pollQueue();

    // Same registered domain, so this passes the onUpdated redirect check and
    // schedules a real capture attempt that will simply never answer.
    ctx.fireTabsUpdated(TAB_ID, { status: "complete" }, { url: "https://www.linkedin.com/jobs/search-results/" });
    await flushAsync();

    // By the time the extension gives up, the tab has drifted to a sign-in page.
    ctx.chrome.tabs.get.mockResolvedValueOnce({ id: TAB_ID, url: "https://www.linkedin.com/login" });
    env.fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) });

    ctx.fireAlarm(`jha-capture-timeout-${TAB_ID}`);
    await flushAsync();

    const call = captureFailedCall(env.fetchMock);
    expect(call).toBeTruthy();
    const body = JSON.parse(call[1].body);
    expect(body.needs_login).toBe(true);
    expect(body.queue_url).toBe("https://www.linkedin.com/jobs/search-results/");
    expect(ctx.chrome.tabs.remove).toHaveBeenCalledWith(TAB_ID);
  });

  it("reports a plain timeout when the final url isn't a login wall", async () => {
    const env = new MockChromeEnvironment();
    const ctx = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({
        items: [{ url: "https://jobs.example.com/search", purpose: "import", source_id: 12 }],
      }),
    });
    ctx.chrome.tabs.create.mockResolvedValueOnce({ id: TAB_ID });
    await ctx.globals.pollQueue();

    ctx.fireTabsUpdated(TAB_ID, { status: "complete" }, { url: "https://jobs.example.com/search" });
    await flushAsync();

    ctx.chrome.tabs.get.mockResolvedValueOnce({ id: TAB_ID, url: "https://jobs.example.com/search" });
    env.fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) });

    ctx.fireAlarm(`jha-capture-timeout-${TAB_ID}`);
    await flushAsync();

    const call = captureFailedCall(env.fetchMock);
    expect(call).toBeTruthy();
    const body = JSON.parse(call[1].body);
    expect(body.needs_login).toBe(false);
    expect(body.reason).toMatch(/gave up waiting/i);
  });
});
