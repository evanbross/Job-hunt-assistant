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
import type { Contact } from "../types/contacts.ts";

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
        if (url === "/api/contacts/outreach-template") return Promise.resolve(jsonResponse({ template: "Hi {first_name}" }));
        if (url.endsWith("/outreach-draft")) return Promise.resolve(jsonResponse({ draft: "Hi there" }));
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
      />,
    );

    expect(screen.getByText("Messaging 1 selected contact")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to full queue" }));
    expect(onClearTarget).toHaveBeenCalledTimes(1);
  });

  it("keeps the default queue to 'Yes' contacts with nothing sent", () => {
    render(
      <OutreachQueue contacts={CONTACTS} targetIds={null} onClearTarget={() => {}} onOutreachLogged={() => Promise.resolve()} />,
    );

    expect(screen.getByText("LinkedIn outreach queue")).toBeInTheDocument();
    expect(screen.getByText("Ada Default")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to full queue" })).not.toBeInTheDocument();
  });
});
