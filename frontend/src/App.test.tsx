/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-26
// Covers App's section-switch guard: with unsaved edits in the Contacts
// section's Templates tab, switching section or logging out asks first.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import type { OutreachTemplate } from "./types/contacts.ts";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

const TEMPLATE: OutreachTemplate = { id: 7, title: "Coworkers", body: "Hi {first_name}", created_at: "t", updated_at: "t" };
const LEAVE_PROMPT = "You have unsaved changes. Leave this page and discard them?";

// ContactsPanel is lazy-loaded, and the first test to open it pays for the
// module load, so allow longer than findBy's 1s default.
const findTemplatesTab = () => screen.findByRole("button", { name: "Templates" }, { timeout: 10000 });

const RESPONSES: Record<string, unknown> = {
  "/api/session": { authenticated: true },
  "/api/setup/status": { complete: true },
  "/api/auth/status": { authenticated: false },
  "/api/contacts": { contacts: [] },
  "/api/application-profile": { previous_employers: [] },
  "/api/outreach-templates": { templates: [TEMPLATE] },
  "/api/logout": { ok: true },
};

describe("App unsaved-changes guard", () => {
  let confirmAnswer: boolean;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("activeTab", "contacts");
    confirmAnswer = false;
    vi.stubGlobal("confirm", vi.fn(() => confirmAnswer));
    // Everything this test doesn't care about (import status, stats, the
    // section switched to) gets a 404, which those panels already tolerate.
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = RESPONSES[url.split("?")[0]];
        return Promise.resolve(body === undefined ? { ok: false, status: 404, json: () => Promise.resolve({}) } : jsonResponse(body));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const editTemplate = async () => {
    render(<App />);
    fireEvent.click(await findTemplatesTab());
    const message = await screen.findByLabelText("Template message");
    await waitFor(() => expect(message).toHaveValue("Hi {first_name}"));
    fireEvent.change(message, { target: { value: "edited" } });
    return message;
  };

  it("switches section without asking when nothing is unsaved", async () => {
    render(<App />);
    fireEvent.click(await findTemplatesTab());
    await screen.findByLabelText("Template message");
    fireEvent.click(screen.getByRole("button", { name: "Phrase Book" }));

    expect(window.confirm).not.toHaveBeenCalled();
    expect(localStorage.getItem("activeTab")).toBe("phrasebook");
  });

  it("stays in the section when the user keeps their unsaved changes", async () => {
    await editTemplate();
    fireEvent.click(screen.getByRole("button", { name: "Phrase Book" }));

    expect(window.confirm).toHaveBeenCalledWith(LEAVE_PROMPT);
    expect(screen.getByLabelText("Template message")).toHaveValue("edited");
    expect(localStorage.getItem("activeTab")).toBe("contacts");
  });

  it("switches section once the user discards them", async () => {
    confirmAnswer = true;
    await editTemplate();
    fireEvent.click(screen.getByRole("button", { name: "Phrase Book" }));

    await waitFor(() => expect(screen.queryByLabelText("Template message")).not.toBeInTheDocument());
    expect(localStorage.getItem("activeTab")).toBe("phrasebook");
  });

  it("does not log out while the user keeps their unsaved changes", async () => {
    await editTemplate();
    fireEvent.click(screen.getByRole("button", { name: /log ?out/i }));

    expect(window.confirm).toHaveBeenCalledWith(LEAVE_PROMPT);
    expect(fetch).not.toHaveBeenCalledWith("/api/logout", expect.anything());
    expect(screen.getByLabelText("Template message")).toHaveValue("edited");
  });
});
