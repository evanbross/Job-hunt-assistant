/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-21
// Covers the picked-contacts mode behind the Contacts table's "Message"
// buttons: picked contacts are messaged in the given order even when the
// default queue would exclude them (not "Yes", or already contacted).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OutreachQueue from "./OutreachQueue.tsx";
import type { Contact, OutreachTemplate } from "../types/contacts.ts";

function contact(overrides: Partial<Contact> & { id: number; full_name: string }): Contact {
  return {
    company_raw: "Acme",
    company_normalized: "acme",
    position: "Engineer",
    profile_url: `https://www.linkedin.com/in/person-${overrides.id}`,
    interested: null,
    outreach: [],
    outreach_score: 1,
    ...overrides,
  };
}

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

// ai: claude-opus-5-5 | 2026-10-21
const TEMPLATE: OutreachTemplate = { id: 7, title: "Coworkers", body: "Hi {first_name}", created_at: "t", updated_at: "t" };

const CONTACTS: Contact[] = [
  contact({ id: 1, full_name: "Ada Default", interested: true, outreach_score: 9 }),
  contact({
    id: 2,
    full_name: "Ben Followup",
    outreach: [{ id: 50, sent_at: "2026-10-01T12:00:00Z", channel: "linkedin" }],
  }),
  contact({ id: 3, full_name: "Cy Noprofile", profile_url: null }),
  contact({ id: 4, full_name: "Dee Undecided" }),
];

describe("OutreachQueue picked contacts", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        // ai: claude-opus-5-5 | 2026-10-21 -- named templates
        if (url === "/api/outreach-templates") return Promise.resolve(jsonResponse({ templates: [TEMPLATE] }));
        if (url.endsWith("/outreach-draft/7")) return Promise.resolve(jsonResponse({ draft: "Hi there" }));
        if (url.endsWith("/outreach")) return Promise.resolve(jsonResponse({ id: 99, sent_at: "2026-10-21T00:00:00Z", channel: "linkedin" }));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("walks the picked contacts in the given order, skipping ones with no profile URL", async () => {
    const onOutreachLogged = vi.fn(() => Promise.resolve());
    render(
      <OutreachQueue
        contacts={CONTACTS}
        targetIds={[2, 3, 4]}
        onClearTarget={() => {}}
        onOutreachLogged={onOutreachLogged}
        onManageTemplates={() => {}}
      />,
    );

    expect(screen.getByText("Messaging 3 selected contacts")).toBeInTheDocument();
    expect(screen.getByText(/1 selected have no LinkedIn profile URL/)).toBeInTheDocument();
    // Already contacted, so the default queue would never show this person.
    expect(screen.getByText("Ben Followup")).toBeInTheDocument();
    expect(screen.getByText(/Already contacted 1x/)).toBeInTheDocument();
    expect(screen.queryByText("Ada Default")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "I sent it" }));

    await waitFor(() => expect(screen.getByText("Dee Undecided")).toBeInTheDocument());
    expect(screen.queryByText("Ben Followup")).not.toBeInTheDocument();
    expect(onOutreachLogged).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      "/api/contacts/2/outreach",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ channel: "linkedin" }) }),
    );
  });

  it("offers a way back to the default queue", () => {
    const onClearTarget = vi.fn();
    render(
      <OutreachQueue
        contacts={CONTACTS}
        targetIds={[4]}
        onClearTarget={onClearTarget}
        onOutreachLogged={() => Promise.resolve()}
        onManageTemplates={() => {}}
      />,
    );

    expect(screen.getByText("Messaging 1 selected contact")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to full queue" }));
    expect(onClearTarget).toHaveBeenCalledTimes(1);
  });

  it("keeps the default queue to 'Yes' contacts with nothing sent", () => {
    render(
      <OutreachQueue contacts={CONTACTS} targetIds={null} onClearTarget={() => {}} onOutreachLogged={() => Promise.resolve()} onManageTemplates={() => {}} />,
    );

    expect(screen.getByText("LinkedIn outreach queue")).toBeInTheDocument();
    expect(screen.getByText("Ada Default")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to full queue" })).not.toBeInTheDocument();
  });
});

// ai: claude-opus-5-5 | 2026-10-21
describe("OutreachQueue named templates", () => {
  const RECRUITERS: OutreachTemplate = { id: 8, title: "Recruiters", body: "Hello", created_at: "t", updated_at: "t" };
  let templates: OutreachTemplate[];

  beforeEach(() => {
    localStorage.clear();
    templates = [TEMPLATE, RECRUITERS];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/api/outreach-templates" && init?.method === "POST") {
          const created = { id: 9, created_at: "t", updated_at: "t", ...JSON.parse(String(init.body)) };
          return Promise.resolve({ ...jsonResponse(created), status: 201 });
        }
        if (url === "/api/outreach-templates") return Promise.resolve(jsonResponse({ templates }));
        const draftMatch = /\/outreach-draft\/(\d+)$/.exec(url);
        if (draftMatch) return Promise.resolve(jsonResponse({ draft: `draft from ${draftMatch[1]}` }));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const renderQueue = () =>
    render(
      <OutreachQueue contacts={CONTACTS} targetIds={[4]} onClearTarget={() => {}} onOutreachLogged={() => Promise.resolve()} onManageTemplates={() => {}} />,
    );

  it("drafts from the first template, then redrafts and remembers the one picked", async () => {
    renderQueue();
    const draft = screen.getByLabelText("Message to Dee Undecided");
    await waitFor(() => expect(draft).toHaveValue("draft from 7"));

    fireEvent.change(screen.getByLabelText("Template"), { target: { value: "8" } });

    await waitFor(() => expect(draft).toHaveValue("draft from 8"));
    expect(localStorage.getItem("outreachTemplateId")).toBe("8");
  });

  it("starts from the remembered template", async () => {
    localStorage.setItem("outreachTemplateId", "8");
    renderQueue();
    await waitFor(() => expect(screen.getByLabelText("Message to Dee Undecided")).toHaveValue("draft from 8"));
  });

  // ai: claude-opus-5-5 | 2026-10-26 -- the editor moved to the Contacts section's Templates tab
  it("sends the user to the Templates tab when there are no templates", async () => {
    templates = [];
    const onManageTemplates = vi.fn();
    render(
      <OutreachQueue
        contacts={CONTACTS}
        targetIds={[4]}
        onClearTarget={() => {}}
        onOutreachLogged={() => Promise.resolve()}
        onManageTemplates={onManageTemplates}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Create a template" }));
    expect(onManageTemplates).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Message to Dee Undecided")).toHaveValue("");
    expect(screen.queryByLabelText("Template name")).not.toBeInTheDocument();
  });
});
