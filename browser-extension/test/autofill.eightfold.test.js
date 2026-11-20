/*
 * =============================================================================
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 * =============================================================================
 */

// ai: claude-opus-5-5 | 2026-11-19
// Eightfold's apply form (confirmed live on lockheedmartin.eightfold.ai): each
// question's combobox is an <input role="combobox" placeholder="Select"> tied to
// its question only through aria-labelledby. A click doesn't open the list;
// typing or ArrowDown does. Typing doesn't filter it, and Enter commits the first
// option starting with the typed text. Autofill read every dropdown's question as
// "Select", applied a saved "Select" -> "No" to all of them, and committed "No" /
// "None of the Above" wherever it typed.
import { describe, expect, it } from "vitest";
import { loadAutofillScript } from "./contentHarness.js";

const APPLY_URL = "https://lockheedmartin.eightfold.ai/careers/apply?pid=1";

const QUESTIONS = [
  { id: "age", question: "Are you 16 years or older?", options: ["Yes", "No"] },
  { id: "auth", question: "Are you legally authorized to work in the country of the job?", options: ["No", "Yes"] },
  {
    id: "visa",
    question: "If yes, please indicate Visa status",
    options: ["A citizen of the U.S.", "A U.S. asylee", "None of the Above"],
  },
];

const FORM_HTML = `<form>${QUESTIONS.map(({ id, question }) => `
  <div class="field">
    <div id="lbl-${id}">${question}</div>
    <div><div><input type="text" role="combobox" id="${id}" placeholder="Select"
      aria-labelledby="lbl-${id}" aria-controls="list-${id}" aria-expanded="false"></div></div>
  </div>`).join("")}
  <button type="button" id="submit">Submit</button>
</form>`;

function wireEightfoldComboboxes(page) {
  const { document, window } = page;
  const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  for (const { id, options } of QUESTIONS) {
    const input = document.getElementById(id);
    let committed = "";
    const listbox = () => document.getElementById(`list-${id}`);
    const close = () => {
      listbox()?.remove();
      input.setAttribute("aria-expanded", "false");
    };
    const commit = (text) => {
      committed = text;
      setValue.call(input, text);
      close();
    };
    const open = () => {
      if (listbox()) return;
      const list = document.createElement("ul");
      list.id = `list-${id}`;
      list.setAttribute("role", "listbox");
      for (const text of options) {
        const option = document.createElement("li");
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(text === committed));
        option.textContent = text;
        option.addEventListener("click", () => commit(text));
        list.appendChild(option);
      }
      document.body.appendChild(list);
      input.setAttribute("aria-expanded", "true");
    };
    input.addEventListener("input", open);
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") open();
      if (event.key === "Escape") {
        close();
        setValue.call(input, committed);
      }
      if (event.key === "Enter" && listbox()) {
        const typed = input.value.toLowerCase();
        const first = options.find((text) => text.toLowerCase().startsWith(typed));
        if (first) commit(first);
      }
    });
  }
}

function openForm() {
  const page = loadAutofillScript({
    url: APPLY_URL,
    html: FORM_HTML,
    respondTo: (message) => (message.action === "learnAnswers" ? { ok: true, answers: [] } : null),
  });
  wireEightfoldComboboxes(page);
  return page;
}

const value = (page, id) => page.document.getElementById(id).value;

describe("Eightfold comboboxes", () => {
  it("reads each question from aria-labelledby, so a saved 'Select' answer applies to none of them", async () => {
    const page = openForm();

    await page.runAutofill({
      profile: { work_authorized: true },
      answers: [
        { id: 16, question_text: "Select", answer_text: "No" },
        { id: 20, question_text: "Are you 16 years or older?", answer_text: "Yes" },
      ],
    });

    expect([value(page, "age"), value(page, "auth"), value(page, "visa")]).toEqual(["Yes", "Yes", ""]);
  });

  it("never commits an option that doesn't match the answer", async () => {
    const page = openForm();
    // A plain <label for>, so the question resolves the same with or without
    // aria-labelledby support: this isolates the option-matching rule.
    const label = page.document.createElement("label");
    label.htmlFor = "visa";
    label.textContent = "If yes, please indicate Visa status";
    page.document.getElementById("lbl-visa").replaceWith(label);

    // Matches no visa option. Typing "No" and pressing Enter would have
    // committed "None of the Above", and the old fallback took the first option.
    await page.runAutofill({
      profile: {},
      answers: [{ id: 21, question_text: "If yes, please indicate Visa status", answer_text: "No" }],
    });

    expect(value(page, "visa")).toBe("");
    expect(page.document.getElementById("visa").getAttribute("aria-expanded")).toBe("false");
  });
});
