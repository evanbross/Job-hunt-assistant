/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-09
// Covers Settings > System > Accounts (plan 4.5): it lists accounts, offers only the
// actions that apply to each (never disabling yourself, resending only to an unfinished
// registration), calls routes/admin_accounts.py's routes, and shows an account's failed tasks.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AccountsPanel from "./AccountsPanel.tsx";
import type { AdminAccount, TaskFailure } from "../types/auth.ts";

const ACCOUNTS_URL = "/api/settings/system/accounts";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

function account(id: number, email: string, status: AdminAccount["status"], role = "user"): AdminAccount {
  return {
    id,
    email,
    name: null,
    role,
    status,
    created_at: "2026-11-01T12:00:00+00:00",
    last_login_at: null,
    disabled_at: status === "disabled" ? "2026-11-09T12:00:00+00:00" : null,
    deletion_requested_at: null,
  };
}

function rowFor(email: string): HTMLElement {
  const row = screen.getByText(email).closest("tr");
  if (!row) throw new Error(`no row for ${email}`);
  return row;
}

describe("AccountsPanel", () => {
  let accounts: AdminAccount[];
  let failures: TaskFailure[];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    accounts = [
      account(1, "boss@example.com", "active", "admin"),
      account(2, "worker@example.com", "active"),
      account(3, "newbie@example.com", "pending_verification"),
    ];
    failures = [
      {
        id: 9,
        task_name: "routes.scan.run_scan_task",
        task_id: "t-1",
        error_type: "ValueError",
        error_message: "could not parse mail for [email]",
        failed_at: "2026-11-09T10:00:00+00:00",
      },
    ];
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal("alert", vi.fn());
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === ACCOUNTS_URL && method === "GET") return Promise.resolve(jsonResponse({ accounts, you: 1 }));
      const action = url.match(/^\/api\/settings\/system\/accounts\/(\d+)\/([a-z-]+)$/);
      if (action && method === "POST") {
        const id = Number(action[1]);
        if (action[2] === "disable" || action[2] === "enable") {
          const status = action[2] === "disable" ? "disabled" : "active";
          accounts = accounts.map((a) => (a.id === id ? { ...a, status } : a));
          return Promise.resolve(jsonResponse({ status: `${action[2]}d` }));
        }
        if (action[2] === "resend-verification") return Promise.resolve(jsonResponse({ status: "sent" }));
      }
      if (action && action[2] === "task-failures" && method === "GET") {
        return Promise.resolve(jsonResponse({ failures: Number(action[1]) === 2 ? failures : [] }));
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists accounts, and offers no way to disable your own", async () => {
    render(<AccountsPanel />);

    await screen.findByText("worker@example.com");
    expect(within(rowFor("boss@example.com")).queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
    expect(within(rowFor("worker@example.com")).getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(within(rowFor("newbie@example.com")).getByText("Hasn't finished registering")).toBeInTheDocument();
  });

  it("disables an account through its route, then offers to enable it", async () => {
    render(<AccountsPanel />);
    await screen.findByText("worker@example.com");

    fireEvent.click(within(rowFor("worker@example.com")).getByRole("button", { name: "Disable" }));

    expect(await within(rowFor("worker@example.com")).findByRole("button", { name: "Enable" })).toBeInTheDocument();
    expect(within(rowFor("worker@example.com")).getByText("Disabled")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`${ACCOUNTS_URL}/2/disable`, expect.objectContaining({ method: "POST" }));
  });

  it("does nothing when the disable confirmation is cancelled", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    render(<AccountsPanel />);
    await screen.findByText("worker@example.com");

    fireEvent.click(within(rowFor("worker@example.com")).getByRole("button", { name: "Disable" }));

    expect(fetchMock).not.toHaveBeenCalledWith(`${ACCOUNTS_URL}/2/disable`, expect.anything());
  });

  it("resends a link only for an unfinished registration", async () => {
    render(<AccountsPanel />);
    await screen.findByText("newbie@example.com");

    expect(within(rowFor("worker@example.com")).queryByRole("button", { name: "Resend link" })).not.toBeInTheDocument();
    fireEvent.click(within(rowFor("newbie@example.com")).getByRole("button", { name: "Resend link" }));

    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(`${ACCOUNTS_URL}/3/resend-verification`, expect.objectContaining({ method: "POST" })),
    );
  });

  it("shows an account's failed tasks", async () => {
    render(<AccountsPanel />);
    await screen.findByText("worker@example.com");

    fireEvent.click(within(rowFor("worker@example.com")).getByRole("button", { name: "Failed tasks" }));

    const section = await screen.findByRole("region", { name: "Failed tasks" });
    expect(await within(section).findByText("run_scan_task")).toBeInTheDocument();
    expect(within(section).getByText(/could not parse mail for \[email\]/)).toBeInTheDocument();
  });

  it("says when an account has no failed tasks", async () => {
    render(<AccountsPanel />);
    await screen.findByText("newbie@example.com");

    fireEvent.click(within(rowFor("newbie@example.com")).getByRole("button", { name: "Failed tasks" }));

    expect(await screen.findByText("No failed tasks recorded.")).toBeInTheDocument();
  });
});
