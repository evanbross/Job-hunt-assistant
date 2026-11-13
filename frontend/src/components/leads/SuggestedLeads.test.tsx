/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Suggested leads inbox: lists what GET /api/lead-suggestions returns, dismisses,
// accepts with the AI's guesses pre-filled, and uploads an export. Fixtures follow
// routes/lead_suggestions.py's real response shapes; names are invented.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SuggestedLeads from "./SuggestedLeads.tsx";
import type { LeadSuggestion, LeadSuggestionsResponse, MessagesImportResponse } from "../../types/leads.ts";

const SUGGESTION: LeadSuggestion = {
  id: 4,
  source: "linkedin_messages",
  external_ref: "c-sam",
  status: "suggested",
  detection: "ai",
  reason: "Pitches a role",
  counterpart_name: "Sam Stranger",
  counterpart_profile_url: "https://www.linkedin.com/in/sam-s",
  contact_id: null,
  job_id: null,
  conversation_title: null,
  role_guess: "Principal Engineer",
  hiring_company_guess: "Acme",
  recruiter_company_guess: "Agency Co",
  first_message_at: "2026-11-04T09:00:00+00:00",
  last_message_at: "2026-11-04T12:00:00+00:00",
  message_count: 2,
  messages: [
    { external_ref: "c-sam/1", sent_at: "2026-11-04T09:00:00+00:00", sender_name: "Sam Stranger", from_owner: false, subject: "Principal role", text: "We're hiring" },
    { external_ref: "c-sam/2", sent_at: "2026-11-04T12:00:00+00:00", sender_name: "Pat Seeker", from_owner: true, subject: "", text: "Interested" },
  ],
  imported_at: "2026-11-11T10:00:00+00:00",
  decided_at: null,
};

const LISTING: LeadSuggestionsResponse = {
  suggestions: [SUGGESTION],
  counts: { suggested: 1, unclassified: 2, not_recruiter: 0, accepted: 0, dismissed: 0 },
  check: { running: false, checked: 0, suggested: 0, error: null, message: "" },
};

const IMPORTED: MessagesImportResponse = {
  conversations: 5, candidates: 4, added: 4, refreshed: 0, messages_logged: 0,
  skipped: { started_by_you: 1 }, unreadable_rows: 0, waiting_for_ai: 2, check_started: true,
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

describe("SuggestedLeads", () => {
  let calls: { url: string; method: string; body: unknown }[];

  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method || "GET";
        const body = init?.body instanceof FormData ? init.body : init?.body ? JSON.parse(String(init.body)) : null;
        calls.push({ url, method, body });
        if (url.startsWith("/api/lead-suggestions?")) return Promise.resolve(jsonResponse(LISTING));
        if (url === "/api/lead-suggestions/linkedin-messages") return Promise.resolve(jsonResponse(IMPORTED, 201));
        if (url === "/api/lead-suggestions/4/accept") {
          return Promise.resolve(jsonResponse({ contact_id: 9, job_id: 31, messages_logged: 2 }));
        }
        return Promise.resolve(jsonResponse({ ok: true }));
      }),
    );
    // useServerEvent opens no stream without EventSource (jsdom has none).
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists suggestions with the AI's read, shows the conversation, and dismisses", async () => {
    render(<SuggestedLeads leads={[]} onAccepted={() => {}} />);

    expect(await screen.findByRole("link", { name: "Sam Stranger" })).toBeInTheDocument();
    expect(screen.getByText("Principal Engineer at Acme")).toBeInTheDocument();
    expect(screen.getByText("AI: Pitches a role")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not checked yet (2)" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show 2 messages" }));
    expect(screen.getByText("Interested")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/lead-suggestions/4/dismiss" && c.method === "POST")).toBe(true));
  });

  it("accepts into a new lead with the guesses pre-filled", async () => {
    const onAccepted = vi.fn();
    render(<SuggestedLeads leads={[]} onAccepted={onAccepted} />);

    fireEvent.click(await screen.findByRole("button", { name: "Accept…" }));
    expect(await screen.findByLabelText("Hiring company")).toHaveValue("Acme");
    expect(screen.getByLabelText("Role")).toHaveValue("Principal Engineer");
    expect(screen.getByRole("radio", { name: "Add to a lead I already track" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Their title"), { target: { value: "Recruiter" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() => expect(onAccepted).toHaveBeenCalledWith({ contact_id: 9, job_id: 31, messages_logged: 2 }));
    expect(calls.find((c) => c.url === "/api/lead-suggestions/4/accept")?.body).toEqual({
      lead: "new",
      company: "Acme",
      job_title: "Principal Engineer",
      contact_company: "Agency Co",
      contact_position: "Recruiter",
    });
  });

  it("uploads an export and says what happened", async () => {
    render(<SuggestedLeads leads={[]} onAccepted={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Import LinkedIn messages" }));
    const file = new File(["CONVERSATION ID\n"], "export.zip", { type: "application/zip" });
    fireEvent.change(await screen.findByLabelText(/Export file/), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Conversations active in"), { target: { value: "180" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    expect(await screen.findByText(/4 of 5 conversations could be someone reaching out/)).toBeInTheDocument();
    expect(screen.getByText("Left out: 1 started by you.")).toBeInTheDocument();
    expect(screen.getByText(/The AI is checking 2 conversations now/)).toBeInTheDocument();
    const form = calls.find((c) => c.url === "/api/lead-suggestions/linkedin-messages")?.body as FormData;
    expect([form.get("active_within_days"), form.get("use_ai"), (form.get("file") as File).name]).toEqual([
      "180", "true", "export.zip",
    ]);
  });
});
