/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// The Application Profile tab's switch for the extension's AI suggestions:
// it reads and writes the autofill_ai_suggestions user setting through
// GET/PUT /api/config, as routes/settings.py stores generic settings (strings).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApplicationProfilePanel from "./ApplicationProfilePanel.tsx";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

const TOGGLE_LABEL = /Let AI suggest answers/;

describe("ApplicationProfilePanel AI suggestions switch", () => {
  let setting: string | undefined;
  let putStatus: number;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setting = undefined;
    putStatus = 200;
    vi.stubGlobal("alert", vi.fn());
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/application-profile") return Promise.resolve(jsonResponse(null));
      if (url === "/api/application-answers") return Promise.resolve(jsonResponse([]));
      if (url === "/api/config" && method === "GET") {
        return Promise.resolve(jsonResponse(setting === undefined ? {} : { autofill_ai_suggestions: setting }));
      }
      if (url === "/api/config" && method === "PUT") {
        if (putStatus >= 400) return Promise.resolve(jsonResponse({ error: "Not saved" }, putStatus));
        setting = JSON.parse(String(init?.body)).autofill_ai_suggestions;
        return Promise.resolve(jsonResponse({ autofill_ai_suggestions: setting }));
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is on by default and saves turning it off", async () => {
    render(<ApplicationProfilePanel />);
    const toggle = await screen.findByRole("checkbox", { name: TOGGLE_LABEL });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(setting).toBe("false");
  });

  it("shows a saved off setting", async () => {
    setting = "false";
    render(<ApplicationProfilePanel />);
    expect(await screen.findByRole("checkbox", { name: TOGGLE_LABEL })).not.toBeChecked();
  });

  it("stays as it was when saving fails", async () => {
    putStatus = 500;
    render(<ApplicationProfilePanel />);
    const toggle = await screen.findByRole("checkbox", { name: TOGGLE_LABEL });

    fireEvent.click(toggle);

    await waitFor(() => expect(window.alert).toHaveBeenCalledWith("Not saved"));
    expect(toggle).toBeChecked();
  });
});
