/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// autofill.js's learning loop: saved answers fill every control type (and an
// exact one overrides the built-in profile rules), and after "Fill
// Application" every answer the person types, picks or corrects shows up in
// one review panel to save -- or is stashed for the popup if they submit first.
import { describe, expect, it, vi } from "vitest";
import { loadAutofillScript } from "./contentHarness.js";

const APPLY_URL = "https://jobs.lever.co/acme/123/apply";

const FORM_HTML = `
<form>
  <div><label for="city">City</label><input type="text" id="city"></div>
  <div><label for="email">Email</label><input type="email" id="email"></div>
  <div><label for="notice">What is your notice period?</label><input type="text" id="notice"></div>
  <div><label for="clearance">Do you hold a security clearance?</label>
    <select id="clearance">
      <option value="">Select...</option>
      <option value="a1">Yes, active</option>
      <option value="a2">No</option>
    </select>
  </div>
  <div>
    <div>Willing to work onsite?</div>
    <div>
      <label><input type="radio" name="onsite" value="opt1">Yes</label>
      <label><input type="radio" name="onsite" value="opt2">No</label>
    </div>
  </div>
  <div><label for="why">Why this company?</label><textarea id="why"></textarea></div>
  <button type="button" id="submit">Submit application</button>
</form>`;

const PROFILE = { city: "Mebane", email: "me@example.com" };

function openForm({ respondTo } = {}) {
  return loadAutofillScript({
    url: APPLY_URL,
    html: FORM_HTML,
    respondTo: respondTo || ((message) => (message.action === "learnAnswers" ? { ok: true, answers: [] } : null)),
  });
}

function typeInto(page, id, value) {
  const field = page.document.getElementById(id);
  const proto = field instanceof page.window.HTMLTextAreaElement
    ? page.window.HTMLTextAreaElement.prototype
    : page.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(field, value);
  field.dispatchEvent(new page.window.Event("input", { bubbles: true }));
  field.dispatchEvent(new page.window.Event("change", { bubbles: true }));
}

function choose(page, id, value) {
  const select = page.document.getElementById(id);
  select.value = value;
  select.dispatchEvent(new page.window.Event("change", { bubbles: true }));
}

const panel = (page) => page.document.getElementById("jha-answer-review")?.shadowRoot ?? null;

async function waitForPanelTitle(page, text) {
  await vi.waitFor(() => expect(panel(page)?.querySelector(".title")?.textContent).toBe(text), { timeout: 2000 });
}

describe("saved answers", () => {
  it("an answer saved for the exact question overrides the profile rule", async () => {
    const page = openForm();
    // Saved with a required-field asterisk; the live label has none.
    await page.runAutofill({ profile: PROFILE, answers: [{ question_text: "City*", answer_text: "Raleigh" }] });

    expect(page.document.getElementById("city").value).toBe("Raleigh");
    expect(page.document.getElementById("email").value).toBe("me@example.com");
  });

  it("fills dropdowns and radio questions, not just text boxes", async () => {
    const page = openForm();
    const results = await page.runAutofill({
      profile: PROFILE,
      answers: [
        { question_text: "Do you hold a security clearance?", answer_text: "Yes, active" },
        { question_text: "Willing to work onsite?", answer_text: "No" },
      ],
    });

    expect(page.document.getElementById("clearance").value).toBe("a1");
    expect(page.document.querySelector('input[value="opt2"]').checked).toBe(true);
    expect(results.filled).toEqual(expect.arrayContaining(["Do you hold a security clearance?", "Willing to work onsite?"]));
  });

  // ai: claude-opus-5-5 | 2026-11-17
  it("matches a merged answer by any of its wordings, exactly", async () => {
    const page = openForm();
    await page.runAutofill({
      profile: PROFILE,
      answers: [{
        id: 7,
        question_text: "Current city",
        answer_text: "Raleigh",
        aliases: [{ id: 70, question_text: "City" }],
      }],
    });

    // An exact alias match overrides the profile rule, as an exact question does.
    expect(page.document.getElementById("city").value).toBe("Raleigh");
  });

  // ai: claude-opus-5-5 | 2026-11-17
  it("reports each saved answer it filled once, for the usage counts", async () => {
    const page = openForm();
    await page.runAutofill({
      profile: PROFILE,
      answers: [
        { id: 4, question_text: "What is your notice period?", answer_text: "2 weeks" },
        { id: 5, question_text: "Do you hold a security clearance?", answer_text: "No", aliases: [{ id: 50, question_text: "Why this company?" }] },
        { id: 6, question_text: "Unasked question?", answer_text: "x" },
      ],
    });

    const reports = page.sentMessages.filter((m) => m.action === "recordAnswerUse");
    expect(reports).toHaveLength(1);
    expect(reports[0].answerIds.sort()).toEqual([4, 5]);
  });

  // ai: claude-opus-5-5 | 2026-11-17
  it("reports nothing when no saved answer was used", async () => {
    const page = openForm();
    await page.runAutofill({ profile: PROFILE, answers: [] });

    expect(page.sentMessages.some((m) => m.action === "recordAnswerUse")).toBe(false);
  });
});

describe("answer review panel", () => {
  it("offers new answers and corrections, and saves the ticked ones in one batch", async () => {
    const page = openForm();
    await page.runAutofill({ profile: PROFILE, answers: [] });
    expect(page.document.getElementById("city").value).toBe("Mebane");

    typeInto(page, "city", "Raleigh");
    typeInto(page, "notice", "2 weeks");
    choose(page, "clearance", "a2");
    page.document.querySelector('input[value="opt1"]').click();

    await waitForPanelTitle(page, "Remember 4 answers for next time?");
    panel(page).querySelector('[data-action="toggle"]').click();
    const rows = [...panel(page).querySelectorAll(".item")].map((row) => row.textContent);
    expect(rows).toContain("CityFilled: MebaneYou changed it to: Raleigh");

    panel(page).querySelector('[data-action="save"]').click();
    await vi.waitFor(() => expect(panel(page)?.querySelector(".status")?.textContent).toBe("Saved 4 answers."));

    const learn = page.sentMessages.find((m) => m.action === "learnAnswers");
    expect(learn.platform).toBe("Lever");
    expect(learn.answers).toEqual(expect.arrayContaining([
      { question_text: "City", answer_text: "Raleigh" },
      { question_text: "What is your notice period?", answer_text: "2 weeks" },
      { question_text: "Do you hold a security clearance?", answer_text: "No" },
      { question_text: "Willing to work onsite?", answer_text: "Yes" },
    ]));
    expect(learn.answers).toHaveLength(4);
    expect(page.sentMessages).toContainEqual({ action: "clearStashedAnswers", pageUrl: APPLY_URL });
  });

  it("offers nothing for values autofill would have produced anyway", async () => {
    const page = openForm();
    await page.runAutofill({
      profile: PROFILE,
      answers: [{ question_text: "What is your notice period?", answer_text: "2 weeks" }],
    });

    typeInto(page, "city", "Mebane");
    typeInto(page, "notice", "2 weeks");
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(panel(page)).toBeNull();
  });

  it("leaves a failed save on screen with the error", async () => {
    const page = openForm({
      respondTo: (message) => (message.action === "learnAnswers" ? { ok: false, error: "Is Job Hunt App running?" } : null),
    });
    await page.runAutofill({ profile: PROFILE, answers: [] });
    typeInto(page, "notice", "2 weeks");
    await waitForPanelTitle(page, "Remember 1 answer for next time?");

    panel(page).querySelector('[data-action="toggle"]').click();
    panel(page).querySelector('[data-action="save"]').click();

    await vi.waitFor(() => expect(panel(page).querySelector(".status.error")?.textContent).toBe("Is Job Hunt App running?"));
    expect(panel(page).querySelector(".title").textContent).toBe("Remember 1 answer for next time?");
  });

  it("starts long, job-specific answers unticked", async () => {
    const page = openForm();
    await page.runAutofill({ profile: PROFILE, answers: [] });
    typeInto(page, "why", "I love what you build. ".repeat(20));
    await waitForPanelTitle(page, "Remember 1 answer for next time?");

    panel(page).querySelector('[data-action="toggle"]').click();

    expect(panel(page).querySelector('.item input[type="checkbox"]').checked).toBe(false);
    expect(panel(page).querySelector('[data-action="save"]').disabled).toBe(true);
  });

  it("stashes ticked, unsaved answers when the application is submitted", async () => {
    const page = openForm();
    await page.runAutofill({ profile: PROFILE, answers: [] });
    typeInto(page, "notice", "2 weeks");
    typeInto(page, "why", "I love what you build. ".repeat(20));

    page.document.getElementById("submit").click();

    const stash = page.sentMessages.find((m) => m.action === "stashPendingAnswers");
    expect(stash).toMatchObject({
      pageUrl: APPLY_URL,
      platform: "Lever",
      answers: [{ question_text: "What is your notice period?", answer_text: "2 weeks" }],
    });
  });
});

// ai: claude-opus-5-5 | 2026-11-17
describe("AI suggestions for unfilled questions", () => {
  function openWithSuggestions(suggestions) {
    return openForm({
      respondTo: (message) => {
        if (message.action === "suggestAnswers") {
          const byQuestion = Object.fromEntries(message.questions.map((q) => [q.question, q.id]));
          return {
            ok: true,
            suggestions: Object.entries(suggestions)
              .filter(([question]) => byQuestion[question])
              .map(([question, answer]) => ({ id: byQuestion[question], answer })),
          };
        }
        return message.action === "learnAnswers" ? { ok: true } : null;
      },
    });
  }

  it("sends only the still-empty questions, with each choice question's options", async () => {
    const page = openWithSuggestions({});
    page.document.getElementById("why").value = "Typed before filling";

    await page.runAutofill({ profile: PROFILE, answers: [] });

    const request = page.sentMessages.find((m) => m.action === "suggestAnswers");
    // Order follows control discovery, not the page.
    expect(Object.fromEntries(request.questions.map((q) => [q.question, q.options]))).toEqual({
      "What is your notice period?": [],
      "Do you hold a security clearance?": ["Yes, active", "No"],
      "Willing to work onsite?": ["Yes", "No"],
    });
  });

  it("fills suggestions in blue and lists them unticked until checked", async () => {
    const page = openWithSuggestions({
      "What is your notice period?": "2 weeks",
      "Do you hold a security clearance?": "No",
    });

    const results = await page.runAutofill({ profile: PROFILE, answers: [] });

    expect(page.document.getElementById("notice").value).toBe("2 weeks");
    expect(page.document.getElementById("clearance").value).toBe("a2");
    expect(page.document.getElementById("notice").style.outline).toBe("2px solid #3b82f6");
    expect(results.suggested).toEqual(["What is your notice period?", "Do you hold a security clearance?"]);
    expect(results.unresolved).toEqual(["Willing to work onsite?", "Why this company?"]);

    await waitForPanelTitle(page, "Remember 2 answers for next time?");
    panel(page).querySelector('[data-action="toggle"]').click();
    expect(panel(page).querySelector(".item").textContent).toContain("AI suggested: 2 weeks");
    expect([...panel(page).querySelectorAll('.item input[type="checkbox"]')].map((b) => b.checked)).toEqual([false, false]);

    // Correcting a suggestion makes it a normal, ticked correction.
    typeInto(page, "notice", "4 weeks");
    await vi.waitFor(() =>
      expect(panel(page).querySelector(".item").textContent).toContain("AI suggested: 2 weeksYou changed it to: 4 weeks")
    );
    expect(panel(page).querySelector('.item input[type="checkbox"]').checked).toBe(true);
  });

  // ai: claude-opus-5-5 | 2026-11-17
  it("shows the AI busy badge only while the request is out, and tells the popup", async () => {
    let badgeDuringRequest = null;
    let page = null;
    page = openForm({
      respondTo: (message) => {
        if (message.action !== "suggestAnswers") return null;
        badgeDuringRequest = page.document.getElementById("jha-ai-busy")?.shadowRoot.textContent ?? null;
        return { ok: true, suggestions: [] };
      },
    });

    await page.runAutofill({ profile: PROFILE, answers: [] });

    expect(badgeDuringRequest).toContain("AI");
    expect(badgeDuringRequest).toContain("Asking AI about 4 questions");
    expect(page.document.getElementById("jha-ai-busy")).toBeNull();
    const busy = page.sentMessages.filter((m) => m.action === "autofillAiBusy").map((m) => m.busy);
    expect(busy).toEqual([true, false]);
  });

  it("reports a failed suggestion request without affecting what was filled", async () => {
    const page = openForm({
      respondTo: (message) => (message.action === "suggestAnswers" ? { ok: false, error: "No API key configured." } : null),
    });

    const results = await page.runAutofill({ profile: PROFILE, answers: [] });

    expect(results.suggestionError).toBe("No API key configured.");
    expect(results.filled).toEqual(["City", "Email"]);
    expect(results.suggested).toEqual([]);
    expect(page.document.getElementById("jha-ai-busy")).toBeNull();
  });
});

// ai: claude-opus-5-5 | 2026-11-18
// Lever's EEO section: the question's <label> wraps the <select>, and a
// browser's innerText of a select is its whole option list. Saved questions
// came out as "Race Select ... Hispanic or Latino ..." and, for a select with
// no label, as its own "Select..." placeholder.
describe("dropdown questions", () => {
  const EEO_HTML = `
<form>
  <label class="application-question">
    <div class="application-label">Race</div>
    <div class="application-field">
      <select id="race">
        <option value="">Select ...</option>
        <option value="Hispanic or Latino">Hispanic or Latino</option>
        <option value="Decline">Decline to self-identify</option>
      </select>
    </div>
  </label>
  <div class="application-question">
    <div class="application-label">Veteran status</div>
    <div class="application-field">
      <select id="veteran">
        <option value="">Select...</option>
        <option value="no">I am not a protected veteran</option>
        <option value="decline">I don't wish to answer</option>
      </select>
    </div>
  </div>
</form>`;

  it("are named by their question, never by their own options", async () => {
    const page = loadAutofillScript({
      url: APPLY_URL,
      html: EEO_HTML,
      respondTo: (message) => (message.action === "learnAnswers" ? { ok: true, answers: [] } : null),
    });
    await page.runAutofill({ profile: PROFILE, answers: [] });

    choose(page, "race", "Decline");
    choose(page, "veteran", "decline");
    await waitForPanelTitle(page, "Remember 2 answers for next time?");
    panel(page).querySelector('[data-action="toggle"]').click();
    panel(page).querySelector('[data-action="save"]').click();
    await vi.waitFor(() => expect(page.sentMessages.some((m) => m.action === "learnAnswers")).toBe(true));

    const learn = page.sentMessages.find((m) => m.action === "learnAnswers");
    expect(learn.answers).toEqual([
      { question_text: "Race", answer_text: "Decline to self-identify" },
      { question_text: "Veteran status", answer_text: "I don't wish to answer" },
    ]);
  });
});
