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

// ai: claude-opus-5-5 | 2026-11-20
// The US citizenship status select: fit scoring's citizenship-requirement
// bonus reads it, so a saved value must reach PUT /api/application-profile as
// the enum value, and "Not set" as null (never a guessed status).
describe("ApplicationProfilePanel US citizenship status", () => {
  let savedProfile: Record<string, unknown> | null;

  beforeEach(() => {
    savedProfile = null;
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (url === "/api/application-profile" && method === "PUT") {
          savedProfile = JSON.parse(String(init?.body));
          return Promise.resolve(jsonResponse(savedProfile));
        }
        if (url === "/api/application-profile") return Promise.resolve(jsonResponse(savedProfile));
        if (url === "/api/application-answers") return Promise.resolve(jsonResponse([]));
        if (url === "/api/config") return Promise.resolve(jsonResponse({}));
        return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("saves an unset status as null", async () => {
    render(<ApplicationProfilePanel />);
    expect(await screen.findByLabelText("US citizenship status")).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => expect(savedProfile).not.toBeNull());
    expect(savedProfile?.us_citizenship_status).toBeNull();
  });

  it("saves the chosen status as its enum value", async () => {
    render(<ApplicationProfilePanel />);
    const select = await screen.findByLabelText("US citizenship status");

    fireEvent.change(select, { target: { value: "citizen_by_birth" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => expect(savedProfile?.us_citizenship_status).toBe("citizen_by_birth"));
    expect(await screen.findByLabelText("US citizenship status")).toHaveValue("citizen_by_birth");
  });
});
