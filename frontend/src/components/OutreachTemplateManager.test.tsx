/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-26
// Covers the Contacts section's Templates tab: it loads the templates itself,
// opens on the one the outreach queue last used, and creates/edits/deletes.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OutreachTemplateManager from "./OutreachTemplateManager.tsx";
import type { OutreachTemplate } from "../types/contacts.ts";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

const COWORKERS: OutreachTemplate = { id: 7, title: "Coworkers", body: "Hi {first_name}", created_at: "t", updated_at: "t" };
const RECRUITERS: OutreachTemplate = { id: 8, title: "Recruiters", body: "Hello", created_at: "t", updated_at: "t" };

describe("OutreachTemplateManager", () => {
  let templates: OutreachTemplate[];

  beforeEach(() => {
    localStorage.clear();
    templates = [COWORKERS, RECRUITERS];
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (url === "/api/outreach-templates" && method === "GET") return Promise.resolve(jsonResponse({ templates }));
        if (url === "/api/outreach-templates" && method === "POST") {
          return Promise.resolve(jsonResponse({ id: 9, created_at: "t", updated_at: "t", ...JSON.parse(String(init?.body)) }, 201));
        }
        const idMatch = /^\/api\/outreach-templates\/(\d+)$/.exec(url);
        if (idMatch && method === "PUT") {
          return Promise.resolve(jsonResponse({ id: Number(idMatch[1]), created_at: "t", updated_at: "t2", ...JSON.parse(String(init?.body)) }));
        }
        if (idMatch && method === "DELETE") return Promise.resolve(jsonResponse({ ok: true }));
        return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens on the template the outreach queue last used", async () => {
    localStorage.setItem("outreachTemplateId", "8");
    render(<OutreachTemplateManager onDirtyChange={() => {}} />);

    await waitFor(() => expect(screen.getByLabelText("Template name")).toHaveValue("Recruiters"));
    expect(screen.getByLabelText("Template message")).toHaveValue("Hello");
  });

  it("starts a new template when there are none", async () => {
    templates = [];
    render(<OutreachTemplateManager onDirtyChange={() => {}} />);

    const name = await screen.findByLabelText("Template name");
    fireEvent.change(name, { target: { value: "Close coworkers" } });
    fireEvent.change(screen.getByLabelText("Template message"), { target: { value: "Hey {first_name}" } });
    fireEvent.click(screen.getByRole("button", { name: "Create template" }));

    await waitFor(() => expect(screen.getByLabelText("Editing")).toHaveValue("9"));
    expect(fetch).toHaveBeenCalledWith(
      "/api/outreach-templates",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "Close coworkers", body: "Hey {first_name}" }),
      }),
    );
    expect(screen.getByRole("button", { name: "Save template" })).toBeDisabled();
  });

  it("saves edits to an existing template", async () => {
    render(<OutreachTemplateManager onDirtyChange={() => {}} />);

    const message = await screen.findByLabelText("Template message");
    await waitFor(() => expect(message).toHaveValue("Hi {first_name}"));
    fireEvent.change(message, { target: { value: "Hi again {first_name}" } });
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));

    await waitFor(() => expect(screen.getByText('Saved the "Coworkers" template.')).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      "/api/outreach-templates/7",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ title: "Coworkers", body: "Hi again {first_name}" }),
      }),
    );
  });

  it("reports unsaved changes as they come and go", async () => {
    const onDirtyChange = vi.fn();
    const { unmount } = render(<OutreachTemplateManager onDirtyChange={onDirtyChange} />);

    const message = await screen.findByLabelText("Template message");
    await waitFor(() => expect(message).toHaveValue("Hi {first_name}"));
    fireEvent.change(message, { target: { value: "edited" } });
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);

    fireEvent.change(message, { target: { value: "edited again" } });
    unmount();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("deletes a template and opens the next one", async () => {
    render(<OutreachTemplateManager onDirtyChange={() => {}} />);

    await waitFor(() => expect(screen.getByLabelText("Template name")).toHaveValue("Coworkers"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.getByLabelText("Template name")).toHaveValue("Recruiters"));
    expect(fetch).toHaveBeenCalledWith("/api/outreach-templates/7", expect.objectContaining({ method: "DELETE" }));
    expect(screen.queryByRole("option", { name: "Coworkers" })).not.toBeInTheDocument();
  });
});
