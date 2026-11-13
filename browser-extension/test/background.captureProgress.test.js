/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-29
// A queue capture that keeps reporting progress (a Phenom search fetching
// every job's page) must not be abandoned by the 1.5-minute capture alarm;
// a tab with no such alarm (a popup capture) must not gain one.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

const ALARM = "jha-capture-timeout-42";

describe("capture progress from a tab", () => {
  it("restarts that tab's capture-timeout alarm", async () => {
    const bg = await new MockChromeEnvironment().loadBackgroundScript();
    bg.chrome.alarms.create(ALARM, { delayInMinutes: 1.5 });
    bg.chrome.alarms.create.mockClear();

    bg.dispatchMessage({ action: "progress", text: "Fetching job details... (3/52)" }, { tab: { id: 42 } });
    await flushAsync();

    expect(bg.chrome.alarms.create).toHaveBeenCalledWith(ALARM, { delayInMinutes: 1.5 });
  });

  it("leaves a tab without a pending queue capture alone", async () => {
    const bg = await new MockChromeEnvironment().loadBackgroundScript();
    bg.chrome.alarms.create.mockClear();

    bg.dispatchMessage({ action: "progress", text: "Listing jobs..." }, { tab: { id: 43 } });
    await flushAsync();

    expect(bg.chrome.alarms.create).not.toHaveBeenCalled();
  });
});
