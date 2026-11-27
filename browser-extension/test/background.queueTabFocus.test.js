/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-27
// pollQueue()'s choice of active vs. background tab for a queued capture. An
// automatic_browser career-site capture (source_id set) is just as unattended
// as a closed-req check, so it must not steal the user's foreground tab --
// confirmed live: a LinkedIn source stuck retrying every scheduler tick (see
// browser_capture_queue.py's claim_pending comment) was doing exactly that.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment } from "./chromeMock.js";

async function pollQueueWith(item) {
  const env = new MockChromeEnvironment();
  const ctx = await env.loadBackgroundScript();
  env.fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [item] }) });
  ctx.chrome.tabs.create.mockResolvedValueOnce({ id: 99 });
  await ctx.globals.pollQueue();
  return ctx.chrome.tabs.create.mock.calls[0][0];
}

describe("pollQueue tab focus", () => {
  it("opens an automatic_browser career-site capture in the background", async () => {
    const created = await pollQueueWith({
      url: "https://www.linkedin.com/jobs/search/?keywords=engineer",
      purpose: "import",
      source_id: 12,
    });
    expect(created).toMatchObject({ active: false });
  });

  it("still steals focus for a plain (non-queue-origin) import capture", async () => {
    const created = await pollQueueWith({
      url: "https://example.com/jobs/123",
      purpose: "import",
      source_id: null,
    });
    expect(created).toMatchObject({ active: true });
  });

  it("keeps the Wellfound exception even for an automatic_browser capture", async () => {
    const created = await pollQueueWith({
      url: "https://wellfound.com/jobs/999-engineer",
      purpose: "import",
      source_id: 12,
    });
    expect(created).toMatchObject({ active: true });
  });

  it("still backgrounds a closed-req check (pre-existing behavior, unaffected)", async () => {
    const created = await pollQueueWith({
      url: "https://example.com/jobs/123",
      purpose: "closed_req",
      job_id: 5,
      source_id: null,
    });
    expect(created).toMatchObject({ active: false });
  });
});
