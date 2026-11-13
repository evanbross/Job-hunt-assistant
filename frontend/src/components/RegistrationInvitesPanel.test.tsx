/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-07
// Covers Settings > System > Invites: it lists invites, adds and revokes them with
// the request shapes routes/registration_invites.py expects (the email always in the
// body, never the URL), and warns when registration isn't invite-only.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RegistrationInvitesPanel from "./RegistrationInvitesPanel.tsx";
import type { RegistrationInvite } from "../types/auth.ts";

const INVITES_URL = "/api/settings/system/registration-invites";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

describe("RegistrationInvitesPanel", () => {
  let invites: RegistrationInvite[];
  let inviteOnly: boolean;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    invites = [{ email: "friend@example.com", invited_by: 1, created_at: "2026-11-07T12:00:00+00:00" }];
    inviteOnly = true;
    vi.stubGlobal("confirm", vi.fn(() => true));
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === INVITES_URL && method === "GET") return Promise.resolve(jsonResponse({ invites, invite_only: inviteOnly }));
      if (url === INVITES_URL && method === "POST") {
        const { email } = JSON.parse(String(init?.body));
        invites = [...invites, { email, invited_by: 1, created_at: "2026-11-07T13:00:00+00:00" }];
        return Promise.resolve(jsonResponse(invites[invites.length - 1], 201));
      }
      if (url === `${INVITES_URL}/revoke` && method === "POST") {
        const { email } = JSON.parse(String(init?.body));
        invites = invites.filter((invite) => invite.email !== email);
        return Promise.resolve(jsonResponse({ status: "revoked" }));
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the open invites", async () => {
    render(<RegistrationInvitesPanel />);

    expect(await screen.findByText("friend@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("adds an invite with the email in the body", async () => {
    render(<RegistrationInvitesPanel />);
    await screen.findByText("friend@example.com");

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: " pal@example.com " } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    expect(await screen.findByText("pal@example.com")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([url, init]) => url === INVITES_URL && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ email: "pal@example.com" });
  });

  it("revokes an invite through the revoke route, not a URL holding the email", async () => {
    render(<RegistrationInvitesPanel />);
    await screen.findByText("friend@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(screen.queryByText("friend@example.com")).not.toBeInTheDocument());
    const revoke = fetchMock.mock.calls.find(([url]) => url === `${INVITES_URL}/revoke`);
    expect(JSON.parse(String(revoke?.[1]?.body))).toEqual({ email: "friend@example.com" });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("%40"))).toBe(false);
  });

  it("warns when anyone can register", async () => {
    inviteOnly = false;
    render(<RegistrationInvitesPanel />);

    expect(await screen.findByRole("status")).toHaveTextContent("anyone can register");
  });
});
