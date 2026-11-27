/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// background.js's "Add as Job Board to Scan": posts the active tab's current
// URL (whatever search filters the user applied are already baked into it)
// and title to the listener's /career-sites route.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

async function addCareerSite(background) {
  const [result] = background
    .dispatchMessage({ action: "addCareerSite", tabId: 7 })
    .filter((r) => r.keptOpen === true);
  await flushAsync();
  return result?.response();
}

async function backgroundOnTab(tab) {
  const env = new MockChromeEnvironment();
  const background = await env.loadBackgroundScript();
  background.chrome.tabs.get.mockResolvedValue({ id: 7, ...tab });
  return { env, background };
}

describe("addCareerSite", () => {
  it("posts the tab's url and title to the listener", async () => {
    const { env, background } = await backgroundOnTab({
      url: "https://acme.example/careers?remote=true",
      title: "Remote Engineering Jobs | Acme",
    });
    env.fetchMock.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        source: { id: 9, name: "Remote Engineering Jobs | Acme", url: "https://acme.example/careers?remote=true" },
        already_added: false,
      }),
    });

    const response = await addCareerSite(background);

    expect(response).toEqual({
      ok: true,
      source: { id: 9, name: "Remote Engineering Jobs | Acme", url: "https://acme.example/careers?remote=true" },
      already_added: false,
    });
    const [url, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/career-sites"));
    expect(url).toMatch(/\/career-sites$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      name: "Remote Engineering Jobs | Acme",
      url: "https://acme.example/careers?remote=true",
    });
  });

  it("falls back to the hostname when the tab has no title", async () => {
    const { env, background } = await backgroundOnTab({ url: "https://acme.example/careers", title: "" });
    env.fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ source: { id: 9, name: "acme.example", url: "https://acme.example/careers" }, already_added: true }),
    });

    await addCareerSite(background);

    const [, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/career-sites"));
    expect(JSON.parse(init.body).name).toBe("acme.example");
  });

  it("reports when the search was already tracked", async () => {
    const { env, background } = await backgroundOnTab({ url: "https://acme.example/careers", title: "Acme" });
    env.fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ source: { id: 9, name: "Acme", url: "https://acme.example/careers" }, already_added: true }),
    });

    expect(await addCareerSite(background)).toEqual({
      ok: true,
      source: { id: 9, name: "Acme", url: "https://acme.example/careers" },
      already_added: true,
    });
  });

  it("refuses a page that isn't http(s)", async () => {
    const { env, background } = await backgroundOnTab({ url: "chrome://extensions/", title: "Extensions" });

    expect(await addCareerSite(background)).toEqual({
      ok: false, error: "This page cannot be added as a job board.",
    });
    expect(env.fetchMock.mock.calls.some(([u]) => String(u).endsWith("/career-sites"))).toBe(false);
  });

  it("reports the listener's refusal", async () => {
    const { env, background } = await backgroundOnTab({ url: "http://127.0.0.1/jobs", title: "Internal" });
    env.fetchMock.mockResolvedValue({
      ok: false, status: 400,
      json: async () => ({ error: "name and url are required, and url must point to a public web address." }),
    });

    expect(await addCareerSite(background)).toEqual({
      ok: false, error: "name and url are required, and url must point to a public web address.",
    });
  });

  it("reports an expired token distinctly", async () => {
    const { env, background } = await backgroundOnTab({ url: "https://acme.example/careers", title: "Acme" });
    env.fetchMock.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    const response = await addCareerSite(background);

    expect(response.ok).toBe(false);
    expect(response.error).toMatch(/extension token/i);
  });
});
