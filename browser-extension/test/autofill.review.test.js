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
