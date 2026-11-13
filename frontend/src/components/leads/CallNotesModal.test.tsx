/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Call-notes import: the review step only proposes, defaults to filling gaps rather
// than overwriting, and applies exactly the ticked changes. Response fixtures follow
// POST /api/leads/<id>/call-notes's real shape (routes/leads.py's import_call_notes).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CallNotesModal from "./CallNotesModal.tsx";
import type { CallNotesImportResponse, LeadDetail } from "../../types/leads.ts";

const LEAD: LeadDetail["lead"] = {
  id: 7,
  company: "Undisclosed",
  job_title: "Staff Engineer",
  url: null,
  source: "Recruiter",
  origin: "recruiter_contact",
  stage: "recruiter_screen",
  date_found: "2026-11-01",
  next_follow_up: null,
  summary_html: "<p>Existing</p>",
  job_description: null,
  notes: null,
};

const RESPONSE: CallNotesImportResponse = {
  interaction: {
    id: 55, contact_id: null, job_id: 7, occurred_at: "2026-11-10T16:00:00+00:00", channel: "video",
    direction: "inbound", kind: "transcript", summary: "Imported call notes", body_html: "<p>notes</p>",
    source: "notetaker", external_ref: null, response_received_at: null, created_at: "2026-11-10T16:00:00+00:00",
  },
  proposal: {
    job_title: "Principal Engineer",
    hiring_company: "Acme <Corp>",
    compensation: "95/hr",
    location: null,
    interview_steps: [],
    next_steps: ["Send resume by Friday"],
    recruiter_requests: [],
    summary_bullets: ["Team of 6"],
    one_line_summary: "Screen with Rita",
    next_follow_up: "2026-11-20",
  },
  ai_error: null,
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

describe("CallNotesModal", () => {
  let calls: { url: string; method: string; body: unknown }[];

  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method || "GET";
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (url === "/api/leads/7/call-notes") return Promise.resolve(jsonResponse(RESPONSE, 201));
        return Promise.resolve(jsonResponse({ ok: true }));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("proposes, fills gaps by default, and applies only the ticked changes", async () => {
    const onChanged = vi.fn();
    const onClose = vi.fn();
    render(<CallNotesModal lead={LEAD} people={[]} onChanged={onChanged} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("Paste the notes"), { target: { value: "We talked about the role" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("Review what the notes say")).toBeInTheDocument();
    expect(calls[0].body).toMatchObject({ text: "We talked about the role", channel: "video" });

    // The role would overwrite an existing title: offered but not ticked. The hiring
    // company replaces "Undisclosed" and the follow-up fills a blank: ticked.
    const role = screen.getByRole("checkbox", { name: /Role: Principal Engineer/ });
    expect(role).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Hiring company: Acme <Corp>/ })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /Follow up on/ }));

    fireEvent.click(screen.getByRole("button", { name: /Apply 3 changes/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const leadPatch = calls.find((c) => c.url === "/api/leads/7" && c.method === "PATCH");
    expect(leadPatch?.body).toMatchObject({ company: "Acme <Corp>" });
    expect(leadPatch?.body).not.toHaveProperty("job_title");
    expect(leadPatch?.body).not.toHaveProperty("next_follow_up");
    const summary = (leadPatch?.body as { summary_html: string }).summary_html;
    expect(summary.startsWith("<p>Existing</p><h3>From call notes")).toBe(true);
    expect(summary).toContain("<li>Team of 6</li><li>Compensation: 95/hr</li><li>Next steps: Send resume by Friday</li>");
    const entryPatch = calls.find((c) => c.url === "/api/interactions/55");
    expect(entryPatch?.body).toEqual({ summary: "Screen with Rita" });
  });

  it("keeps the notes and says so when the AI couldn't read them", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ ...RESPONSE, proposal: null, ai_error: "Rate limited" }, 201))),
    );
    render(<CallNotesModal lead={LEAD} people={[]} onChanged={() => {}} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Paste the notes"), { target: { value: "notes" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/The notes were saved, but the AI couldn't read them: Rate limited/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });
});
