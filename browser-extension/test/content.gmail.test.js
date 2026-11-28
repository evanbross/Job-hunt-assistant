/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-28
// Gmail is otherwise a blocked host (content.js's _NON_JOB_HOSTS) -- it's not
// a job page -- but an open message offers "add this sender" instead of just
// refusing. Fixture markup mirrors Gmail's long-standing (if undocumented)
// .gD[email]/h2.hP DOM markers for "open message sender"/"subject heading".
import { describe, expect, it } from "vitest";
import { loadContentScript } from "./contentHarness.js";

const URL = "https://mail.google.com/mail/u/0/#inbox/abc123";

function openMessageHtml({ senderEmail = "notifications@greenhouse.io", senderName = "Greenhouse Notifications", subject = "New jobs matching your search" } = {}) {
  return `
    <div role="main">
      <h2 class="hP">${subject}</h2>
      <div class="gE">
        <span class="gD" email="${senderEmail}" name="${senderName}">${senderName}</span>
      </div>
    </div>`;
}

// A couple of inbox-list rows, each with their own .gD[email] -- these must
// not win over an actually-open message's sender, and (with no h2.hP at all)
// must not be mistaken for one themselves.
const INBOX_LIST_HTML = `
  <div role="main">
    <span class="gD" email="alerts@linkedin.com" name="LinkedIn">LinkedIn</span>
    <span class="gD" email="jobs@indeed.com" name="Indeed">Indeed</span>
  </div>`;

describe("Gmail sender detection", () => {
  it("offers to add the open message's sender as an email scan source", async () => {
    const { detectPage } = loadContentScript({ url: URL, html: openMessageHtml() });

    const result = await detectPage();

    expect(result).toEqual({
      site: "Gmail",
      type: "gmailMessage",
      jobCount: 0,
      description: "Email from Greenhouse Notifications",
      senderEmail: "notifications@greenhouse.io",
      senderName: "Greenhouse Notifications",
      subject: "New jobs matching your search",
    });
  });

  it("falls back to the email address when the sender has no display name", async () => {
    const html = `
      <div role="main">
        <h2 class="hP">A subject line</h2>
        <span class="gD" email="jobs@example.com">jobs@example.com</span>
      </div>`;
    const { detectPage } = loadContentScript({ url: URL, html });

    const result = await detectPage();

    expect(result.senderEmail).toBe("jobs@example.com");
    expect(result.senderName).toBe("jobs@example.com");
  });

  it("is blocked (not offered) on the inbox list, which has no open message", async () => {
    const { detectPage } = loadContentScript({ url: URL, html: INBOX_LIST_HTML });

    const result = await detectPage();

    expect(result.type).toBe("blocked");
  });

  it("is blocked when a message is open but no sender address can be found", async () => {
    const html = `<div role="main"><h2 class="hP">A subject with no sender markup</h2></div>`;
    const { detectPage } = loadContentScript({ url: URL, html });

    const result = await detectPage();

    expect(result.type).toBe("blocked");
  });
});
