/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-26
// Covers the Contacts section's tab bar guard: leaving the Templates tab with
// unsaved template edits asks first.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ContactsPanel from "./ContactsPanel.tsx";
import type { OutreachTemplate } from "../types/contacts.ts";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

const TEMPLATE: OutreachTemplate = { id: 7, title: "Coworkers", body: "Hi {first_name}", created_at: "t", updated_at: "t" };

describe("ContactsPanel Templates tab", () => {
  let confirmAnswer: boolean;

  beforeEach(() => {
    localStorage.clear();
    confirmAnswer = false;
    vi.stubGlobal("confirm", vi.fn(() => confirmAnswer));
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/contacts") return Promise.resolve(jsonResponse({ contacts: [] }));
        if (url === "/api/application-profile") return Promise.resolve(jsonResponse({ previous_employers: [] }));
        if (url === "/api/outreach-templates") return Promise.resolve(jsonResponse({ templates: [TEMPLATE] }));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const openTemplatesTab = async () => {
    render(<ContactsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    const message = await screen.findByLabelText("Template message");
    await waitFor(() => expect(message).toHaveValue("Hi {first_name}"));
    return message;
  };

  it("leaves without asking when nothing is unsaved", async () => {
    await openTemplatesTab();
    fireEvent.click(screen.getByRole("button", { name: "Companies" }));

    expect(window.confirm).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Template message")).not.toBeInTheDocument();
  });

  it("stays on the tab when the user keeps their unsaved changes", async () => {
    const message = await openTemplatesTab();
    fireEvent.change(message, { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Companies" }));

    expect(window.confirm).toHaveBeenCalledWith("Discard your unsaved changes to this template?");
    expect(screen.getByLabelText("Template message")).toHaveValue("edited");
  });

  it("leaves, and stops asking, once the user discards them", async () => {
    confirmAnswer = true;
    const message = await openTemplatesTab();
    fireEvent.change(message, { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: /^Outreach queue/ }));

    expect(screen.queryByLabelText("Template message")).not.toBeInTheDocument();
    // The unmounted editor cleared the dirty flag, so later tab changes don't ask.
    fireEvent.click(screen.getByRole("button", { name: "Companies" }));
    expect(window.confirm).toHaveBeenCalledTimes(1);
  });
});
