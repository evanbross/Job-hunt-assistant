/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// The Leads board: which leads each filter shows, the stale highlight, and the
// conversations-without-a-role list. Fixtures follow GET /api/leads's real shape
// (backend/leads/lead_service.py's LeadService.board).
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LeadsPanel from "./LeadsPanel.tsx";
import type { LeadSummary, LeadsBoard } from "../types/leads.ts";
import type { ApplicationStageStatus } from "../types/applications.ts";

function lead(overrides: Partial<LeadSummary> & { id: number; company: string }): LeadSummary {
  return {
    job_title: "Engineer",
    url: null,
    origin: "recruiter_contact",
    next_follow_up: null,
    date_found: "2026-11-01",
    latest_stage_date: "2026-11-01",
    primary_contact_id: 5,
    primary_contact_name: "Rita Recruiter",
    primary_contact_company: "Agency Co",
    last_interaction_at: null,
    contact_count: 1,
    stage: "recruiter_screen",
    is_closed: false,
    last_activity_date: "2026-11-01",
    idle_days: 2,
    is_stale: false,
    follow_up_due: false,
    needs_attention: false,
    ...overrides,
  };
}

function stage(key: string, label: string, isTerminal = false): ApplicationStageStatus {
  return { key, label, sort_order: 0, ghost_eligible: false, is_terminal: isTerminal, color_bg: null, color_fg: null };
}

const BOARD: LeadsBoard = {
  leads: [
    lead({ id: 1, company: "Fresh Co" }),
    lead({ id: 2, company: "Quiet Co", is_stale: true, needs_attention: true, idle_days: 14, last_activity_date: "2026-10-20" }),
    lead({ id: 3, company: "Due Co", next_follow_up: "2026-11-18", follow_up_due: true, needs_attention: true }),
    // ai: claude-opus-5-5 | 2026-11-18 -- a date the browser would call past, but the
    // server (on the account's own date) says isn't due: the page follows the server.
    lead({ id: 5, company: "Later Co", next_follow_up: "2000-01-01" }),
    lead({ id: 4, company: "Done Co", stage: "withdrew", is_closed: true }),
  ],
  open_conversations: [
    {
      contact_id: 9,
      full_name: "Talking Tina",
      company_raw: "Staffing Inc",
      position: "Recruiter",
      recruiter_type: "unrated",
      keep_informed: false,
      last_interaction_at: "2026-11-05T10:00:00+00:00",
      last_channel: "phone",
      last_summary: "Has a few roles",
    },
  ],
  stale_after_days: 10,
  open_conversation_window_days: 30,
};

const CATALOG = [stage("recruiter_screen", "Recruiter Screen"), stage("withdrew", "Withdrew", true)];

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

function rowCompanies(): string[] {
  const table = screen.getAllByRole("table")[0];
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].querySelector("strong")?.textContent || "");
}

describe("LeadsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/leads") return Promise.resolve(jsonResponse(BOARD));
        if (url === "/api/contacts") return Promise.resolve(jsonResponse({ contacts: [] }));
        if (url === "/api/applications/stage-statuses") return Promise.resolve(jsonResponse(CATALOG));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows open leads by default, with stage labels and the stale highlight", async () => {
    render(<LeadsPanel />);
    expect(await screen.findByText("Fresh Co")).toBeInTheDocument();
    expect(rowCompanies()).toEqual(["Fresh Co", "Quiet Co", "Due Co", "Later Co"]);
    expect((await screen.findAllByText("Recruiter Screen")).length).toBe(4);
    expect(screen.getByText("Quiet Co").closest("tr")).toHaveClass("stale");
    expect(screen.getByRole("button", { name: "Open (4)" })).toHaveAttribute("aria-pressed", "true");
  });

  // ai: claude-opus-5-5 | 2026-11-18
  it("marks a follow-up due only where the server says so", async () => {
    render(<LeadsPanel />);
    await screen.findByText("Fresh Co");
    expect(within(screen.getByText("Due Co").closest("tr")!).getByText("due")).toBeInTheDocument();
    expect(within(screen.getByText("Later Co").closest("tr")!).queryByText("due")).not.toBeInTheDocument();
  });

  // ai: claude-opus-5-5 | 2026-11-18
  it("reports the needs-attention count for the nav badge each time the board loads", async () => {
    const onAttentionCountChange = vi.fn();
    render(<LeadsPanel onAttentionCountChange={onAttentionCountChange} />);
    await screen.findByText("Fresh Co");
    expect(onAttentionCountChange).toHaveBeenLastCalledWith(2);
  });

  it("filters to leads needing attention and to closed leads", async () => {
    render(<LeadsPanel />);
    await screen.findByText("Fresh Co");

    fireEvent.click(screen.getByRole("button", { name: "Needs attention (2)" }));
    expect(rowCompanies()).toEqual(["Quiet Co", "Due Co"]);

    fireEvent.click(screen.getByRole("button", { name: "Closed (1)" }));
    expect(rowCompanies()).toEqual(["Done Co"]);
  });

  it("searches by company or recruiter", async () => {
    render(<LeadsPanel />);
    await screen.findByText("Fresh Co");
    fireEvent.change(screen.getByLabelText("Search leads"), { target: { value: "quiet" } });
    expect(rowCompanies()).toEqual(["Quiet Co"]);
  });

  it("lists conversations that aren't tied to a role yet", async () => {
    render(<LeadsPanel />);
    expect(await screen.findByText("Talking Tina")).toBeInTheDocument();
    expect(screen.getByText("Has a few roles")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create lead" })).toBeInTheDocument();
  });
});
