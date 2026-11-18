/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// background.js's "Capture this conversation": read the open thread back from
// linkedinMessagesRecorder.js (in the page's own world) and send it to the
// listener. Every name and message here is invented.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

const THREAD = "2-UmVjcnVpdGVyVGhyZWFk==";
const THREAD_URL = `https://www.linkedin.com/messaging/thread/${encodeURIComponent(THREAD)}/`;
const CAPTURE = {
  conversation_id: THREAD,
  counterpart_name: "Rita Recruiter",
  counterpart_profile_url: "https://www.linkedin.com/in/ACoAARita",
  messages: [{ sent_at_ms: 1_762_298_888_637, from_owner: false, sender_name: "Rita Recruiter", subject: "", text: "Hi" }],
};

async function capture(background) {
  const [result] = background
    .dispatchMessage({ action: "captureLinkedInConversation", tabId: 7 })
    .filter((r) => r.keptOpen === true);
  await flushAsync();
  return result?.response();
}

async function backgroundOn(url, recorded) {
  const env = new MockChromeEnvironment();
  const background = await env.loadBackgroundScript();
  background.chrome.tabs.get.mockResolvedValue({ id: 7, url });
  background.chrome.scripting.executeScript.mockResolvedValue([{ result: recorded }]);
  return { env, background };
}

describe("captureLinkedInConversation", () => {
  it("reads the thread in the page's own world and posts it to the listener", async () => {
    const { env, background } = await backgroundOn(THREAD_URL, CAPTURE);
    env.fetchMock.mockResolvedValue({
      ok: true, status: 201, json: async () => ({ suggestion_id: 4, status: "suggested", added: true }),
    });

    const response = await capture(background);

    expect(response).toEqual({ ok: true, suggestion_id: 4, status: "suggested", added: true });
    const [injected] = background.chrome.scripting.executeScript.mock.calls[0];
    expect(injected).toMatchObject({ target: { tabId: 7 }, world: "MAIN", args: [THREAD] });
    const [url, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/linkedin-messages/capture"));
    expect(url).toMatch(/\/linkedin-messages\/capture$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(CAPTURE);
  });

  it("says why when the recorder can't hand the thread over", async () => {
    const { env, background } = await backgroundOn(THREAD_URL, { error: "group" });

    expect(await capture(background)).toEqual({
      ok: false, error: "Only one-to-one conversations can be captured, not group ones.",
    });
    expect(env.fetchMock.mock.calls.some(([u]) => String(u).endsWith("/linkedin-messages/capture"))).toBe(false);
  });

  it("refuses a tab that isn't a LinkedIn conversation", async () => {
    const { background } = await backgroundOn("https://www.linkedin.com/feed/", CAPTURE);

    expect(await capture(background)).toEqual({ ok: false, error: "Open a LinkedIn conversation first." });
    expect(background.chrome.scripting.executeScript).not.toHaveBeenCalledWith(expect.objectContaining({ world: "MAIN" }));
  });

  it("reports the listener's refusal", async () => {
    const { env, background } = await backgroundOn(THREAD_URL, CAPTURE);
    env.fetchMock.mockResolvedValue({
      ok: false, status: 400, json: async () => ({ error: "That doesn't look like a one-to-one LinkedIn conversation." }),
    });

    expect(await capture(background)).toEqual({
      ok: false, error: "That doesn't look like a one-to-one LinkedIn conversation.",
    });
  });
});
