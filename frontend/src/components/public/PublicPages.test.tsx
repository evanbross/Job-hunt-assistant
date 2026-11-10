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
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App.tsx";
import { PRIVACY_CONTACT_EMAIL } from "./publicSite.ts";

function stubSession(session: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url === "/api/session"
          ? { ok: true, status: 200, json: () => Promise.resolve(session) }
          : { ok: false, status: 404, json: () => Promise.resolve({}) },
      ),
    ),
  );
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
});
