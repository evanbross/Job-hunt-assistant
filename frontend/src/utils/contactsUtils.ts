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
// Mirrors normalize_company_name() in backend/company_matching.py — keep the
// suffix list and operation order identical in both places.
export function contactsMatchKey(company?: string | null): string {
  return normalizeCompany(company).replace(/\s+/g, "");
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
