/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// The Leads tab's "needs attention" badge (GET /api/leads/attention) and the
// `?tab=leads` link the follow-up email uses.
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

let attentionCount: number;

function stubBackend(): void {
  const responses: Record<string, unknown> = {
    "/api/session": { authenticated: true },
    "/api/setup/status": { complete: true },
    "/api/auth/status": { authenticated: false },
    "/api/leads/attention": { count: attentionCount },
    "/api/leads": { leads: [], open_conversations: [], stale_after_days: 10, open_conversation_window_days: 30 },
    "/api/contacts": { contacts: [] },
    "/api/applications/stage-statuses": [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = responses[url.split("?")[0]];
      return Promise.resolve(body === undefined ? { ok: false, status: 404, json: () => Promise.resolve({}) } : jsonResponse(body));
    }),
  );
}

describe("App leads badge", { timeout: 20000 }, () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("activeTab", "phrasebook");
    window.history.replaceState({}, "", "/");
    attentionCount = 3;
    stubBackend();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("shows how many leads need attention on the Leads tab", async () => {
    render(<App />);
    expect(await screen.findByLabelText("3 leads need attention")).toHaveTextContent("3");
  });

  it("shows no badge when nothing needs attention", async () => {
    attentionCount = 0;
    stubBackend();
    render(<App />);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/leads/attention", expect.anything()));
    expect(screen.queryByLabelText(/need(s)? attention/)).not.toBeInTheDocument();
  });

  it("opens the Leads tab from ?tab=leads and then drops the parameter", async () => {
    window.history.replaceState({}, "", "/?tab=leads");
    render(<App />);
    expect(await screen.findByRole("button", { name: "New recruiter lead" }, { timeout: 10000 })).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("ignores a ?tab= value that isn't a tab", async () => {
    window.history.replaceState({}, "", "/?tab=nonsense");
    render(<App />);
    await screen.findByLabelText("3 leads need attention");
    expect(localStorage.getItem("activeTab")).toBe("phrasebook");
  });
});
