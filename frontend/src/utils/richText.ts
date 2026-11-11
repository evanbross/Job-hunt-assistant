/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Client-side sanitizing for stored rich-text notes. The server already sanitizes
// every note it stores (backend/utils/rich_text.py); this runs again before anything
// is rendered, with the same allowlist, so neither layer trusts the other.
import DOMPurify from "dompurify";

// Mirrors backend/utils/rich_text.py's _ALLOWED_TAGS / _ALLOWED_ATTRIBUTES.
const ALLOWED_TAGS = [
  "p", "br", "strong", "b", "em", "i", "u", "s", "code", "pre", "blockquote",
  "ul", "ol", "li", "h1", "h2", "h3", "hr", "a",
];
const ALLOWED_ATTR = ["href", "rel"];

/** http(s) and mailto only, as the server allows. */
export const SAFE_LINK_RE = /^(https?:|mailto:)/i;

export function sanitizeRichText(html: string): string {
  return DOMPurify.sanitize(html, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOWED_URI_REGEXP: SAFE_LINK_RE });
}

/** True when a note holds nothing visible (TipTap's empty document is "<p></p>"). */
export function isRichTextEmpty(html: string | null | undefined): boolean {
  if (!html) return true;
  const text = new DOMParser().parseFromString(html, "text/html").body.textContent || "";
  return text.trim() === "" && !html.includes("<hr");
}
