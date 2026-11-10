/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Covers the forced setup page's SMTP section. On a fresh hosted install the first
// verification email always fails (no relay configured yet), and the page must then
// show a usable SMTP form: it used to open the section without fetching anything,
// leaving it on "Loading…" forever.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CompleteSetupPage from "./CompleteSetupPage.tsx";
import type { AccountInfo } from "../types/auth.ts";
import type { SettingsSchema } from "../types/settings.ts";

const ACCOUNT: AccountInfo = {
  name: "",
  email: "admin@localhost",
  role: "admin",
  must_complete_setup: true,
  password_pending_default: false,
  deletion_requested_at: null,
  app_mode: "hosted",
};

const SCHEMA: SettingsSchema = {
  user: [],
  system: [
    { key: "smtp_host", label: "SMTP Host", group: "SMTP", input_type: "text", placeholder: "", help_text: "", is_secret: false },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

describe("CompleteSetupPage SMTP section", () => {
  let systemSettingsStatus: number;

  beforeEach(() => {
    systemSettingsStatus = 200;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        switch (url) {
          case "/api/account":
            return Promise.resolve(jsonResponse(ACCOUNT));
          case "/api/account/request-email-change":
            return Promise.resolve(jsonResponse({ error: "Could not send the verification email" }, 502));
          case "/api/settings/schema":
            return Promise.resolve(jsonResponse(SCHEMA));
          case "/api/settings/system":
            return Promise.resolve(jsonResponse({ smtp_host: "localhost" }, systemSettingsStatus));
          default:
            return Promise.resolve(jsonResponse({}, 404));
        }
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const failVerificationSend = async () => {
    render(<CompleteSetupPage onComplete={() => {}} />);
    fireEvent.change(await screen.findByPlaceholderText("you@example.com"), { target: { value: "me@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send verification link" }));
  };

  it("loads the SMTP form when the verification email can't be sent", async () => {
    await failVerificationSend();

    expect(await screen.findByLabelText("SMTP Host")).toHaveValue("localhost");
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("says so when the SMTP settings can't be loaded, instead of loading forever", async () => {
    systemSettingsStatus = 403;
    await failVerificationSend();

    expect(await screen.findByText(/Could not load the SMTP settings \(HTTP 403\)/)).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });
});
