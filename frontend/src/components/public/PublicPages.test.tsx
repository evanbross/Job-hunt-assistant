/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Which page a visitor gets (App.tsx): Google's verification needs a homepage and a
// privacy policy anyone can open without signing in.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App.tsx";
import { GOVERNING_LAW_STATE, PRIVACY_CONTACT_EMAIL, TERMS_PATH, TERMS_VERSION } from "./publicSite.ts";

type FetchStub = ReturnType<typeof vi.fn>;

// ai: claude-opus-5-5 | 2026-11-10 -- extra routes for the terms tests
function stubSession(session: Record<string, unknown>, routes: Record<string, unknown> = {}): FetchStub {
  const fetchMock = vi.fn((url: string) => {
    const body = url === "/api/session" ? session : routes[url];
    return Promise.resolve(
      body !== undefined
        ? { ok: true, status: 200, json: () => Promise.resolve(body) }
        : { ok: false, status: 404, json: () => Promise.resolve({}) },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function visit(path: string) {
  window.history.pushState({}, "", path);
  render(<App />);
}

describe("public pages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("shows a logged-out visitor the homepage on a hosted server", async () => {
    stubSession({ authenticated: false, app_mode: "hosted" });
    visit("/");

    expect(await screen.findByRole("heading", { name: "How it uses your Gmail" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Privacy policy" })[0]).toHaveAttribute("href", "/privacy");
    expect(screen.getAllByRole("link", { name: "Sign in" })[0]).toHaveAttribute("href", "/login");
  });

  it("takes a hosted visitor at /login straight to the sign-in form", async () => {
    stubSession({ authenticated: false, app_mode: "hosted" });
    visit("/login");

    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
  });

  it("keeps the desktop app going straight to sign-in", async () => {
    stubSession({ authenticated: false, app_mode: "desktop" });
    visit("/");

    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "How it uses your Gmail" })).not.toBeInTheDocument();
  });

  it("serves the privacy policy without signing in", async () => {
    stubSession({ authenticated: false, app_mode: "hosted" });
    visit("/privacy");

    expect(await screen.findByRole("heading", { name: "Privacy policy" })).toBeInTheDocument();
    expect(screen.getByText(/including the Limited Use requirements/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: PRIVACY_CONTACT_EMAIL }).length).toBeGreaterThan(0);
  });

  // ai: claude-opus-5-5 | 2026-11-10
  it("serves the terms of service without signing in, linked from every public page", async () => {
    stubSession({ authenticated: false, app_mode: "hosted" });
    visit(TERMS_PATH);

    expect(await screen.findByRole("heading", { name: "Terms of Service", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`laws of the State of ${GOVERNING_LAW_STATE}`))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", TERMS_PATH);
  });

  it("asks a hosted sign-up to accept the terms, and sends that acceptance", async () => {
    const fetchMock = stubSession(
      { authenticated: false, app_mode: "hosted" },
      { "/api/account/complete-registration": { authenticated: true } },
    );
    visit("/verify-account?token=abc");

    const agree = await screen.findByRole("checkbox", { name: /I agree to the Terms of Service/ });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22hunter" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "hunter22hunter" } });
    fireEvent.click(agree);
    fireEvent.click(screen.getByRole("button", { name: "Set password and sign in" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/account/complete-registration")).toBe(true),
    );
    const [, init] = fetchMock.mock.calls.find(([url]) => url === "/api/account/complete-registration")!;
    expect(JSON.parse(init.body)).toEqual({ token: "abc", password: "hunter22hunter", accept_terms: true });
  });

  it("has no terms checkbox on a desktop sign-up", async () => {
    stubSession({ authenticated: false, app_mode: "desktop" });
    visit("/verify-account?token=abc");

    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("holds a signed-in user at the terms until they accept the version shown", async () => {
    const fetchMock = stubSession(
      { authenticated: true, app_mode: "hosted", must_complete_setup: false, terms_acceptance_required: true },
      { "/api/account/accept-terms": { status: "accepted", version: TERMS_VERSION } },
    );
    visit("/");

    const accept = await screen.findByRole("button", { name: "Accept and continue" });
    expect(accept).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I have read and agree/ }));
    fireEvent.click(accept);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Accept and continue" })).not.toBeInTheDocument(),
    );
    const [, init] = fetchMock.mock.calls.find(([url]) => url === "/api/account/accept-terms")!;
    expect(JSON.parse(init.body)).toEqual({ version: TERMS_VERSION });
  });
});
