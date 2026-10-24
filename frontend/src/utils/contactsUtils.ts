/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

import { normalizeCompany } from "./sheetUtils.ts";

// ai: claude-sonnet-5 | 2026-07-25
// Mirrors normalize_company_name() in backend/scoring/company_matching.py — keep the
// suffix list and operation order identical in both places. (That module also
// builds the SQL copy the Job Queue's "Know someone" filter runs, from the same
// list -- ai: claude-opus-5-5 | 2026-10-23.)
export function contactsMatchKey(company?: string | null): string {
  return normalizeCompany(company).replace(/\s+/g, "");
}

// ai: claude-opus-5-5 | 2026-10-21 -- moved here from OutreachQueue so the
// template manager shares them.
// LinkedIn's direct-message limit; matches routes/contacts.py's
// MAX_OUTREACH_MESSAGE_LENGTH (backend/contacts/outreach_drafts.py).
export const MAX_OUTREACH_MESSAGE_LENGTH = 8000;
// Matches routes/contacts.py's _MAX_OUTREACH_TEMPLATE_TITLE_LENGTH.
export const MAX_OUTREACH_TEMPLATE_TITLE_LENGTH = 100;
// The placeholders backend/contacts/outreach_drafts.py's render_outreach_template() fills.
export const OUTREACH_PLACEHOLDER_HELP = "Placeholders: {first_name}, {full_name}, {company}, {position}";

// Error message from a failed JSON response, or the HTTP status.
export async function responseErrorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body?.error === "string" ? body.error : `HTTP ${res.status}`;
}

// ai: claude-opus-5-5 | 2026-10-14
// JSON write helper shared by the Contacts tab's panels (was copy-pasted into
// ContactsPanel, CompaniesReport, and TitlesReport).
export async function sendJson(url: string, method: string, body?: unknown): Promise<Response> {
  return fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// ai: claude-sonnet-5 | 2026-09-25
// Profile URLs come from the imported CSV, so only http(s) may become a link
// target -- a `javascript:` value in the file must never reach an href.
// ai: claude-opus-5-5 | 2026-10-14 -- moved here from ContactsPanel so the outreach queue shares it.
export function safeHttpUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}
