/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-26
// Covers the Contacts section's tab bar: leaving the Templates tab with
// unsaved template edits asks first, and the Outreach queue tab always opens
// the full queue.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ContactsPanel from "./ContactsPanel.tsx";
import type { Contact, OutreachTemplate } from "../types/contacts.ts";

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
    render(<ContactsPanel onUnsavedChangesChange={() => {}} />);
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

// ai: claude-opus-5-5 | 2026-10-26
describe("ContactsPanel Outreach queue tab", () => {
  const CONTACTS: Contact[] = [1, 2].map((id) => ({
    id,
    full_name: `Person ${id}`,
    company_raw: "Acme",
    company_normalized: "acme",
    position: "Engineer",
    profile_url: `https://www.linkedin.com/in/person-${id}`,
    interested: true,
    outreach: [],
    outreach_score: id,
  }));

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/contacts") return Promise.resolve(jsonResponse({ contacts: CONTACTS }));
        if (url === "/api/application-profile") return Promise.resolve(jsonResponse({ previous_employers: [] }));
        if (url === "/api/outreach-templates") return Promise.resolve(jsonResponse({ templates: [TEMPLATE] }));
        if (url.endsWith("/outreach-draft/7")) return Promise.resolve(jsonResponse({ draft: "Hi there" }));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens the full queue its badge counts, even after messaging one contact", async () => {
    render(<ContactsPanel onUnsavedChangesChange={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Message Person 1" }));
    expect(screen.getByText("Messaging 1 selected contact")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Contacts" }));
    fireEvent.click(screen.getByRole("button", { name: "Outreach queue (2)" }));

    expect(screen.getByText("LinkedIn outreach queue")).toBeInTheDocument();
    expect(screen.getByText("2 to go, highest priority first.")).toBeInTheDocument();
  });
});

// ai: claude-opus-5-5 | 2026-10-26
describe("ContactsPanel filters", () => {
  const CONTACTS: Contact[] = [
    { id: 1, full_name: "Ada Yes", company_raw: "Acme", company_normalized: "acme", position: "Engineer", interested: true, outreach: [] },
    { id: 2, full_name: "Bo No", company_raw: "Globex", company_normalized: "globex", position: "Engineer", interested: false, outreach: [] },
    { id: 3, full_name: "Cy Maybe", company_raw: "Acme", company_normalized: "acme", position: "Engineer", interested: null, outreach: [] },
  ];

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/contacts") return Promise.resolve(jsonResponse({ contacts: CONTACTS }));
        if (url === "/api/application-profile") return Promise.resolve(jsonResponse({ previous_employers: ["Acme"] }));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const renderPanel = async () => {
    const view = render(<ContactsPanel onUnsavedChangesChange={() => {}} />);
    await screen.findByLabelText("Filter by former employer");
    return view;
  };

  const wantToContact = (label: string) =>
    within(screen.getByRole("group", { name: "Filter by want to contact" })).getByRole("button", { name: label });

  it("shows any combination of want-to-contact answers", async () => {
    await renderPanel();
    fireEvent.click(wantToContact("Yes"));
    expect(screen.getByText("1 of 3 contacts")).toBeInTheDocument();

    fireEvent.click(wantToContact("Undecided"));
    expect(wantToContact("Yes")).toHaveAttribute("aria-pressed", "true");
    expect(wantToContact("Undecided")).toHaveAttribute("aria-pressed", "true");
    expect(wantToContact("No")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("2 of 3 contacts")).toBeInTheDocument();
    expect(screen.queryByText("Bo No")).not.toBeInTheDocument();

    // Unpicking the last one goes back to everyone.
    fireEvent.click(wantToContact("Yes"));
    fireEvent.click(wantToContact("Undecided"));
    expect(screen.getByText("3 of 3 contacts")).toBeInTheDocument();
  });

  it("remembers the filters from one visit to the next", async () => {
    const first = await renderPanel();
    fireEvent.click(wantToContact("Yes"));
    fireEvent.click(wantToContact("Undecided"));
    fireEvent.change(screen.getByLabelText("Filter by former employer"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText("Search contacts"), { target: { value: "ada" } });
    first.unmount();

    await renderPanel();
    expect(wantToContact("Yes")).toHaveAttribute("aria-pressed", "true");
    expect(wantToContact("Undecided")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Filter by former employer")).toHaveValue("Acme");
    expect(screen.getByLabelText("Search contacts")).toHaveValue("ada");
    expect(screen.getByText("1 of 3 contacts")).toBeInTheDocument();
  });

  it("clears every filter, and remembers that too", async () => {
    const first = await renderPanel();
    const clear = screen.getByRole("button", { name: "Clear filters" });
    expect(clear).toBeDisabled();
    fireEvent.click(wantToContact("No"));
    fireEvent.change(screen.getByLabelText("Filter by recruiter"), { target: { value: "not_recruiter" } });
    expect(screen.getByText("1 of 3 contacts")).toBeInTheDocument();

    fireEvent.click(clear);
    expect(wantToContact("No")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("Filter by recruiter")).toHaveValue("all");
    expect(screen.getByText("3 of 3 contacts")).toBeInTheDocument();
    expect(clear).toBeDisabled();
    first.unmount();

    await renderPanel();
    expect(screen.getByText("3 of 3 contacts")).toBeInTheDocument();
  });

  it("ignores a remembered value that's no longer an option", async () => {
    localStorage.setItem(
      "contactsFilters",
      JSON.stringify({ interestedFilter: ["maybe", "yes"], companyFilter: "Initech", outreachFilter: "bogus" }),
    );
    await renderPanel();

    expect(wantToContact("Yes")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Filter by former employer")).toHaveValue("");
    expect(screen.getByLabelText("Filter by outreach status")).toHaveValue("all");
    expect(screen.getByText("1 of 3 contacts")).toBeInTheDocument();
  });

  it("still loads a single want-to-contact answer saved before it was multi-choice", async () => {
    localStorage.setItem("contactsFilters", JSON.stringify({ interestedFilter: "tbd" }));
    await renderPanel();
    expect(wantToContact("Undecided")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("1 of 3 contacts")).toBeInTheDocument();
  });

  it("remembers the sort order, and Clear filters leaves it alone", async () => {
    const first = await renderPanel();
    const contactNames = () => screen.getAllByText(/^(Ada Yes|Bo No|Cy Maybe)$/).map((el) => el.textContent);
    fireEvent.click(screen.getByRole("button", { name: "Sort by Contact" }));
    fireEvent.click(screen.getByRole("button", { name: "Sort by Contact" }));
    expect(screen.getByRole("button", { name: "Sort by Contact" })).toHaveTextContent("Contact ▼");
    fireEvent.click(wantToContact("Yes"));
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    first.unmount();

    await renderPanel();
    expect(screen.getByRole("button", { name: "Sort by Contact" })).toHaveTextContent("Contact ▼");
    expect(screen.getByRole("button", { name: "Sort by Priority" })).toHaveTextContent(/^Priority$/);
    expect(contactNames()).toEqual(["Cy Maybe", "Bo No", "Ada Yes"]);
  });

  it("falls back to the default sort for an unknown column", async () => {
    localStorage.setItem("contactsSort", JSON.stringify({ sortKey: "bogus", sortDir: "asc" }));
    await renderPanel();
    expect(screen.getByRole("button", { name: "Sort by Priority" })).toHaveTextContent("Priority ▼");
  });

  it("starts unfiltered when the stored value is corrupt", async () => {
    localStorage.setItem("contactsFilters", "{not json");
    await renderPanel();
    expect(screen.getByText("3 of 3 contacts")).toBeInTheDocument();
  });
});
