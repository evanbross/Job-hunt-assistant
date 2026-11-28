/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-28
// background.js's "Add Sender as Email Scan Source": posts the name/email the
// popup collected (pre-filled from content.js's gmailMessage detection, but
// user-editable) to the listener's /email-sources route. Unlike addCareerSite,
// there's no tab to re-derive this from -- the popup supplies it directly.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

async function addEmailSource(background, name, email) {
  const [result] = background
    .dispatchMessage({ action: "addEmailSource", name, email })
    .filter((r) => r.keptOpen === true);
  await flushAsync();
  return result?.response();
}

describe("addEmailSource", () => {
  it("posts the name and email to the listener", async () => {
    const env = new MockChromeEnvironment();
    const background = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        source: { id: 4, name: "Greenhouse Notifications", match_type: "sender_email", match_value: "notifications@greenhouse.io" },
        already_added: false,
      }),
    });

    const response = await addEmailSource(background, "Greenhouse Notifications", "notifications@greenhouse.io");

    expect(response).toEqual({
      ok: true,
      source: { id: 4, name: "Greenhouse Notifications", match_type: "sender_email", match_value: "notifications@greenhouse.io" },
      already_added: false,
    });
    const [url, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/email-sources"));
    expect(url).toMatch(/\/email-sources$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      name: "Greenhouse Notifications",
      email: "notifications@greenhouse.io",
    });
  });

  it("reports when the sender was already tracked", async () => {
    const env = new MockChromeEnvironment();
    const background = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        source: { id: 4, name: "Greenhouse", match_type: "sender_email", match_value: "notifications@greenhouse.io" },
        already_added: true,
      }),
    });

    const response = await addEmailSource(background, "Greenhouse", "notifications@greenhouse.io");

    expect(response.already_added).toBe(true);
  });

  it("reports the listener's refusal", async () => {
    const env = new MockChromeEnvironment();
    const background = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "name and email are required, and email must look like an email address." }),
    });

    expect(await addEmailSource(background, "", "not-an-email")).toEqual({
      ok: false,
      error: "name and email are required, and email must look like an email address.",
    });
  });

  it("reports an expired token distinctly", async () => {
    const env = new MockChromeEnvironment();
    const background = await env.loadBackgroundScript();
    env.fetchMock.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    const response = await addEmailSource(background, "Greenhouse", "notifications@greenhouse.io");

    expect(response.ok).toBe(false);
    expect(response.error).toMatch(/extension token/i);
  });
});
