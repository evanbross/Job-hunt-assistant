/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// Found live: backend/queue_websocket.py logged a steady stream of 401s, a few
// seconds apart, each with a distinct freshly-issued ticket. Root cause: the
// queue-ready WebSocket's reconnect backoff (_queueSocketReconnectDelayMs) was
// a plain `let`, reset to 1000ms on every service-worker restart -- so a
// flapping worker could never actually escalate its backoff, and a restart
// landing mid-handshake could fire a fresh connectQueueSocket() before the
// prior attempt's ticket had settled. The fix persists the "don't retry
// before this time" gate in chrome.storage.session, which (per chromeMock.js)
// is the one piece of state that survives a simulated worker restart.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

function wsTicketCalls(fetchMock) {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes("/ws-ticket"));
}

function withToken(env) {
  env.syncStore.set("token", "tok123");
  env.syncStore.set("listenerUrl", "http://jha-listener.localhost");
}

describe("queue socket reconnect backoff across a worker restart", () => {
  it("blocks an immediate retry from a freshly restarted worker", async () => {
    const env = new MockChromeEnvironment();
    withToken(env);
    const ctx = await env.loadBackgroundScript();
    const callsAfterFirstLoad = wsTicketCalls(env.fetchMock).length;
    expect(callsAfterFirstLoad).toBeGreaterThan(0); // sanity: the load itself attempted a connection

    // Simulate the attempt failing and a backoff being recorded, then a
    // worker restart landing immediately afterward -- before the backoff
    // window has elapsed.
    ctx.globals.scheduleQueueSocketReconnect(1000);
    await flushAsync();

    await env.loadBackgroundScript();
    await flushAsync();

    expect(wsTicketCalls(env.fetchMock).length).toBe(callsAfterFirstLoad);
  });

  it("resumes attempts once the persisted backoff window has elapsed", async () => {
    const env = new MockChromeEnvironment();
    withToken(env);
    const ctx = await env.loadBackgroundScript();
    const callsAfterFirstLoad = wsTicketCalls(env.fetchMock).length;

    ctx.globals.scheduleQueueSocketReconnect(20);
    await flushAsync();
    await new Promise((resolve) => setTimeout(resolve, 40));

    await env.loadBackgroundScript();
    await flushAsync();

    expect(wsTicketCalls(env.fetchMock).length).toBeGreaterThan(callsAfterFirstLoad);
  });
});
