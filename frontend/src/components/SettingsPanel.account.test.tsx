/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// Settings > Account's "Log out other sessions" button: POST
// /api/account/logout-other-sessions (routes/session_auth.py), only after the
// person confirms, and the outcome is shown either way.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPanel from "./SettingsPanel.tsx";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

const ACCOUNT = {
  name: "Pat",
  email: "pat@example.com",
  role: "user",
  must_complete_setup: false,
  password_pending_default: false,
  deletion_requested_at: null,
  app_mode: "desktop",
  deletion_grace_days: 30,
};

describe("SettingsPanel log out other sessions", () => {
  let logoutStatus: number;
  let fetchMock: ReturnType<typeof vi.fn>;
  let confirmMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    logoutStatus = 200;
    confirmMock = vi.fn(() => true);
    vi.stubGlobal("confirm", confirmMock);
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/account") return Promise.resolve(jsonResponse(ACCOUNT));
      if (url === "/api/account/logout-other-sessions" && init?.method === "POST") {
        return Promise.resolve(
          logoutStatus >= 400
            ? jsonResponse({ error: "Database temporarily unavailable" }, logoutStatus)
            : jsonResponse({ status: "other_sessions_ended" }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function renderAccount() {
    render(
      <SettingsPanel initialSection="account" onSetupComplete={() => {}} isGoogleAuthenticated={false} focusEmailId={null} />,
    );
  }

  const logoutCalls = () => fetchMock.mock.calls.filter(([url]) => url === "/api/account/logout-other-sessions");

  it("ends the other sessions after confirming", async () => {
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Log out other sessions" }));

    expect(await screen.findByText("Other sessions logged out.")).toBeInTheDocument();
    expect(confirmMock).toHaveBeenCalledOnce();
    expect(logoutCalls()).toHaveLength(1);
  });

  it("does nothing when the confirmation is declined", async () => {
    confirmMock.mockReturnValue(false);
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Log out other sessions" }));

    expect(logoutCalls()).toHaveLength(0);
    expect(screen.queryByText("Other sessions logged out.")).not.toBeInTheDocument();
  });

  it("shows the server's error when it fails", async () => {
    logoutStatus = 503;
    renderAccount();
    fireEvent.click(await screen.findByRole("button", { name: "Log out other sessions" }));

    expect(await screen.findByText("Database temporarily unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Other sessions logged out.")).not.toBeInTheDocument();
  });
});
