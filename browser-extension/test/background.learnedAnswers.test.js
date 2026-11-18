/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// background.js's side of autofill learning: relaying the review panel's
// ticked answers to the listener in one batch, and keeping answers stashed on
// submit until the popup saves or discards them.
import { describe, expect, it } from "vitest";
import { MockChromeEnvironment, flushAsync } from "./chromeMock.js";

const ANSWERS = [
  { question_text: "What is your notice period?", answer_text: "2 weeks" },
  { question_text: "Willing to work onsite?", answer_text: "No" },
];

async function send(background, message) {
  const [result] = background.dispatchMessage(message).filter((r) => r.keptOpen === true);
  await flushAsync();
  return result?.response();
}

describe("learnAnswers", () => {
  it("posts the batch to the listener and reports success", async () => {
    const env = new MockChromeEnvironment();
    env.fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ answers: [{ id: 1 }, { id: 2 }] }) });
    const background = await env.loadBackgroundScript();

    const response = await send(background, { action: "learnAnswers", answers: ANSWERS, platform: "Lever" });

    expect(response).toEqual({ ok: true, answers: [{ id: 1 }, { id: 2 }] });
    const [url, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/application-answers"));
    expect(url).toMatch(/\/application-answers$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ answers: ANSWERS, platform: "Lever" });
  });

  it("reports the listener's error instead of claiming success", async () => {
    const env = new MockChromeEnvironment();
    env.fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: "Could not save answers." }) });
    const background = await env.loadBackgroundScript();

    const response = await send(background, { action: "learnAnswers", answers: ANSWERS, platform: null });

    expect(response).toEqual({ ok: false, error: "Could not save your answers (500): Could not save answers." });
  });
});

// ai: claude-opus-5-5 | 2026-11-17
describe("recordAnswerUse", () => {
  it("posts the used answer ids to the listener without a reply to wait on", async () => {
    const env = new MockChromeEnvironment();
    env.fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ recorded: 2 }) });
    const background = await env.loadBackgroundScript();

    const results = background.dispatchMessage({ action: "recordAnswerUse", answerIds: [3, 8] });
    await flushAsync();

    expect(results.some((r) => r.keptOpen === true)).toBe(false);
    const [, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/application-answers/used"));
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ answer_ids: [3, 8] });
  });
});

describe("answers stashed on submit", () => {
  it("keeps one entry per application page until it is cleared", async () => {
    const env = new MockChromeEnvironment();
    const background = await env.loadBackgroundScript();
    const stash = { action: "stashPendingAnswers", pageUrl: "https://jobs.lever.co/acme/1/apply", pageTitle: "Acme", platform: "Lever" };

    background.dispatchMessage({ ...stash, answers: ANSWERS.slice(0, 1) });
    await flushAsync();
    background.dispatchMessage({ ...stash, answers: ANSWERS });
    await flushAsync();

    const stored = await send(background, { action: "getStashedAnswers" });
    expect(stored).toHaveLength(1);
    expect(stored[0]).toMatchObject({ pageUrl: stash.pageUrl, pageTitle: "Acme", platform: "Lever", answers: ANSWERS });
    expect(env.notifications).toHaveLength(1);

    await send(background, { action: "clearStashedAnswers", pageUrl: stash.pageUrl });
    expect(await send(background, { action: "getStashedAnswers" })).toEqual([]);
  });
});

// ai: claude-opus-5-5 | 2026-11-17
describe("suggestAnswers", () => {
  const QUESTIONS = [{ id: "q1", question: "Years of Python?", options: [] }];

  it("asks the listener and passes back its suggestions", async () => {
    const env = new MockChromeEnvironment();
    env.fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ suggestions: [{ id: "q1", answer: "10" }], disabled: false }),
    });
    const background = await env.loadBackgroundScript();

    const response = await send(background, { action: "suggestAnswers", questions: QUESTIONS, pageTitle: "Acme" });

    expect(response).toEqual({ ok: true, suggestions: [{ id: "q1", answer: "10" }], disabled: false });
    const [, init] = env.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/application-answers/suggest"));
    expect(JSON.parse(init.body)).toEqual({ questions: QUESTIONS, page_title: "Acme" });
  });

  it("passes back the listener's error", async () => {
    const env = new MockChromeEnvironment();
    env.fetchMock.mockResolvedValue({ ok: false, status: 502, json: async () => ({ error: "No API key configured." }) });
    const background = await env.loadBackgroundScript();

    const response = await send(background, { action: "suggestAnswers", questions: QUESTIONS, pageTitle: "" });

    expect(response).toEqual({ ok: false, error: "No API key configured." });
  });
});
