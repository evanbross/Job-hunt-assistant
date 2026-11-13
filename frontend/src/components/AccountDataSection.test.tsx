/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Settings > Account > Your data, against the request shapes routes/session_auth.py
// expects: export is a GET download, deletion needs the current password in the body.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AccountDataSection from "./AccountDataSection.tsx";
import type { AccountInfo } from "../types/auth.ts";

const ACCOUNT: AccountInfo = {
  name: "Ada",
  email: "ada@example.com",
  role: "user",
  must_complete_setup: false,
  password_pending_default: false,
  deletion_requested_at: null,
  app_mode: "hosted",
  deletion_grace_days: 30,
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body), blob: () => Promise.resolve(new Blob(["{}"])) };
}

describe("AccountDataSection", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url === "/api/account/request-deletion") {
        return Promise.resolve(jsonResponse({ deletion_requested_at: "2026-11-10T12:00:00+00:00" }));
      }
      return Promise.resolve(jsonResponse({ status: "ok" }));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));
    URL.createObjectURL = vi.fn(() => "blob:export");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("downloads the export from GET /api/account/export", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<AccountDataSection account={ACCOUNT} onDeletionChange={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "Download my data" }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toBe("/api/account/export");
    click.mockRestore();
  });

  it("requests deletion with the current password and reports the new state", async () => {
    const onDeletionChange = vi.fn();
    render(<AccountDataSection account={ACCOUNT} onDeletionChange={onDeletionChange} />);

    fireEvent.change(screen.getByLabelText(/Current password/), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete my account" }));

    await waitFor(() => expect(onDeletionChange).toHaveBeenCalledWith("2026-11-10T12:00:00+00:00"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/request-deletion");
    expect(JSON.parse(init.body)).toEqual({ password: "hunter22" });
  });

  it("does nothing if the user backs out of the confirmation", () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    render(<AccountDataSection account={ACCOUNT} onDeletionChange={() => {}} />);

    fireEvent.change(screen.getByLabelText(/Current password/), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete my account" }));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows a scheduled deletion's date and lets it be cancelled", async () => {
    const onDeletionChange = vi.fn();
    const scheduled = { ...ACCOUNT, deletion_requested_at: "2026-11-10T12:00:00+00:00" };
    render(<AccountDataSection account={scheduled} onDeletionChange={onDeletionChange} />);

    expect(screen.getByText(/scheduled for permanent deletion on/)).toHaveTextContent("2026");
    fireEvent.click(screen.getByRole("button", { name: "Cancel deletion" }));

    await waitFor(() => expect(onDeletionChange).toHaveBeenCalledWith(null));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/account/cancel-deletion");
  });
});
