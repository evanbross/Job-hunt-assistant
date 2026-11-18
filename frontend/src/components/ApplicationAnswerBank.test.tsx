/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// The answer bank's usage counts, sorting, merging, in-place editing and alias
// removal, against the request shapes routes/application_profile.py accepts.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApplicationAnswerBank from "./ApplicationAnswerBank.tsx";
import type { ApplicationAnswer } from "../types/applicationProfile.ts";

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) };
}

function answer(overrides: Partial<ApplicationAnswer>): ApplicationAnswer {
  return {
    id: 1,
    question_text: "Question?",
    question_normalized: "question?",
    answer_text: "Answer",
    platform: null,
    updated_at: "2026-11-10T12:00:00+00:00",
    use_count: 0,
    last_used_at: null,
    aliases: [],
    ...overrides,
  };
}

const SPONSORSHIP = answer({
  id: 1,
  question_text: "Do you need sponsorship?",
  answer_text: "No",
  use_count: 1,
  last_used_at: "2026-11-12T12:00:00+00:00",
  aliases: [{ id: 50, question_text: "Will you require visa sponsorship?" }],
});
const VISA = answer({ id: 2, question_text: "Will you now or in the future need a visa?", answer_text: "No, never", use_count: 4, last_used_at: "2026-11-15T12:00:00+00:00" });
const CLEARANCE = answer({ id: 3, question_text: "Security clearance?", answer_text: "None" });

describe("ApplicationAnswerBank", () => {
  let answers: ApplicationAnswer[];
  let fetchMock: ReturnType<typeof vi.fn>;
  let requests: { url: string; method: string; body: unknown }[];

  beforeEach(() => {
    answers = [SPONSORSHIP, VISA, CLEARANCE];
    requests = [];
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/config") return Promise.resolve(jsonResponse({}));
      if (url === "/api/application-answers" && method === "GET") return Promise.resolve(jsonResponse(answers));
      if (url === "/api/application-answers/merge") {
        answers = [answer({ ...VISA, aliases: [{ id: 60, question_text: SPONSORSHIP.question_text }] }), CLEARANCE];
        return Promise.resolve(jsonResponse(answers[0]));
      }
      if (url === "/api/application-answers/3" && method === "PUT") return Promise.resolve(jsonResponse(CLEARANCE));
      if (url === "/api/application-answers/aliases/50") return Promise.resolve(jsonResponse({ deleted: 50 }));
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const questionOrder = () =>
    screen
      .getAllByRole("checkbox", { name: /^Select/ })
      .map((box) => box.getAttribute("aria-label"));

  it("shows each answer's usage and the other wordings it matches", async () => {
    render(<ApplicationAnswerBank />);
    expect(await screen.findByText("Do you need sponsorship?")).toBeInTheDocument();
    expect(screen.getByText(/^Used once, last on/)).toBeInTheDocument();
    expect(screen.getByText(/^Used 4 times, last on/)).toBeInTheDocument();
    expect(screen.getByText("Not used yet")).toBeInTheDocument();
    expect(screen.getByText(/Will you require visa sponsorship\?/)).toBeInTheDocument();
  });

  it("sorts by most and least used", async () => {
    render(<ApplicationAnswerBank />);
    await screen.findByText("Security clearance?");
    const sort = screen.getByRole("combobox", { name: "Sort saved answers" });

    fireEvent.change(sort, { target: { value: "most_used" } });
    expect(questionOrder()[0]).toContain(VISA.question_text);

    fireEvent.change(sort, { target: { value: "least_used" } });
    expect(questionOrder()[0]).toContain(CLEARANCE.question_text);
  });

  it("filters by question, answer or alias wording", async () => {
    render(<ApplicationAnswerBank />);
    await screen.findByText("Security clearance?");

    fireEvent.change(screen.getByRole("searchbox", { name: "Search saved answers" }), { target: { value: "require visa" } });

    expect(questionOrder()).toEqual([`Select "${SPONSORSHIP.question_text}" to merge`]);
  });

  it("merges the ticked answers into the one picked, with the edited answer", async () => {
    render(<ApplicationAnswerBank />);
    await screen.findByText("Security clearance?");
    const mergeButton = screen.getByRole("button", { name: "Merge" });
    expect(mergeButton).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: `Select "${SPONSORSHIP.question_text}" to merge` }));
    fireEvent.click(screen.getByRole("checkbox", { name: `Select "${VISA.question_text}" to merge` }));
    fireEvent.click(screen.getByRole("button", { name: "Merge 2" }));

    const dialog = screen.getByText("Merge 2 saved answers").closest(".modal-panel") as HTMLElement;
    fireEvent.click(within(dialog).getAllByRole("radio")[1]); // keep VISA
    const merged = within(dialog).getByRole("textbox", { name: "Merged answer" });
    expect(merged).toHaveValue("No, never");
    fireEvent.change(merged, { target: { value: "No" } });
    expect(within(dialog).getByText("Usage adds up: 5 uses in total.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Merge" }));

    await waitFor(() => expect(screen.queryByText("Merge 2 saved answers")).not.toBeInTheDocument());
    expect(requests.find((r) => r.url === "/api/application-answers/merge")?.body).toEqual({
      keep_id: VISA.id,
      merge_ids: [SPONSORSHIP.id],
      answer_text: "No",
    });
    expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
  });

  it("saves an edit in place with PUT", async () => {
    render(<ApplicationAnswerBank />);
    await screen.findByText("Security clearance?");
    const row = screen.getByText("Security clearance?").closest("div[style]")?.parentElement as HTMLElement;

    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByDisplayValue("Security clearance?"), { target: { value: "Do you hold a clearance?" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    expect(requests.find((r) => r.method === "PUT")).toEqual({
      url: "/api/application-answers/3",
      method: "PUT",
      body: { question_text: "Do you hold a clearance?", answer_text: "None", platform: null },
    });
  });

  it("removes an alias", async () => {
    render(<ApplicationAnswerBank />);
    fireEvent.click(await screen.findByRole("button", { name: 'Stop matching "Will you require visa sponsorship?"' }));

    await waitFor(() =>
      expect(requests.some((r) => r.url === "/api/application-answers/aliases/50" && r.method === "DELETE")).toBe(true),
    );
  });
});
