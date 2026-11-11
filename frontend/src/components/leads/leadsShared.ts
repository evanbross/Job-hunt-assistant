/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Helpers shared by the Leads tab's components.
import type { ApplicationStageStatus } from "../../types/applications.ts";
import type { LeadSummary } from "../../types/leads.ts";
import { apiFetch } from "../../utils/apiClient.ts";
import { responseErrorText } from "../../utils/contactsUtils.ts";

/** Today as YYYY-MM-DD in the user's own time zone (what a date input shows). */
export function localToday(): string {
  return new Date().toLocaleDateString("en-CA");
}

/** A stage key's label from the application_stage_statuses catalog. */
export function stageLabel(catalog: ApplicationStageStatus[], key: string | null | undefined): string {
  if (!key) return "—";
  return catalog.find((s) => s.key === key)?.label || key.replace(/_/g, " ");
}

/** GET a JSON body, throwing with the server's error message on a non-2xx. */
export async function getJson<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(await responseErrorText(res));
  return (await res.json()) as T;
}

export async function loadStageCatalog(): Promise<ApplicationStageStatus[]> {
  return getJson<ApplicationStageStatus[]>("/api/applications/stage-statuses");
}

/** Open, and either gone quiet or due for a follow-up today or earlier. */
export function needsAttention(lead: LeadSummary, today: string): boolean {
  return !lead.is_closed && (lead.is_stale || (lead.next_follow_up !== null && lead.next_follow_up <= today));
}

export interface NewContactDraft {
  full_name: string;
  company: string;
  position: string;
  phone: string;
  email_address: string;
  profile_url: string;
}

export function emptyNewContact(): NewContactDraft {
  return { full_name: "", company: "", position: "", phone: "", email_address: "", profile_url: "" };
}

/** The new-contact body routes/leads.py and routes/contacts.py accept: blanks dropped. */
export function newContactBody(draft: NewContactDraft): Record<string, string> {
  const body: Record<string, string> = { full_name: draft.full_name.trim() };
  for (const key of ["company", "position", "phone", "email_address", "profile_url"] as const) {
    const value = draft[key].trim();
    if (value) body[key] = value;
  }
  return body;
}
