/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
import { describe, expect, it } from "vitest";
import { isRichTextEmpty, sanitizeRichText } from "./richText.ts";

describe("sanitizeRichText", () => {
  it("keeps the notes allowlist", () => {
    const html = '<p>Rate <strong>90</strong> <em>/hr</em></p><ul><li>remote</li></ul><a href="https://x.com">link</a>';
    expect(sanitizeRichText(html)).toBe(html);
  });

  it("drops script, event handlers, styles, images and unsafe links", () => {
    const cleaned = sanitizeRichText(
      '<p style="color:red" onclick="x()">t</p><script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">bad</a>',
    );
    expect(cleaned).toBe("<p>t</p><a>bad</a>");
  });
});

describe("isRichTextEmpty", () => {
  it("treats an empty editor as empty", () => {
    expect(isRichTextEmpty("<p></p>")).toBe(true);
    expect(isRichTextEmpty(null)).toBe(true);
    expect(isRichTextEmpty("<p> </p>")).toBe(true);
  });

  it("sees text and a rule as content", () => {
    expect(isRichTextEmpty("<p>x</p>")).toBe(false);
    expect(isRichTextEmpty("<hr>")).toBe(false);
  });
});
