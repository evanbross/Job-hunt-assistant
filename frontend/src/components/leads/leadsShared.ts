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
import type { ContactEmailInput, EmailType } from "../../types/contacts.ts";
import type { CallNotesProposal } from "../../types/leads.ts";
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

export interface NewContactDraft {
  full_name: string;
  company: string;
  position: string;
  phone: string;
  // ai: claude-sonnet-5 | 2026-11-26 -- was email_address/personal_email; one typed
  // address captured at creation time, more can be added afterward in the full editor
  // (ContactDetail.tsx / LeadDrawer.tsx's ContactEmailsEditor).
  email: string;
  email_type: EmailType;
  profile_url: string;
}

export function emptyNewContact(): NewContactDraft {
  return { full_name: "", company: "", position: "", phone: "", email: "", email_type: "other", profile_url: "" };
}

/** The new-contact body routes/leads.py and routes/contacts.py accept: blanks dropped. */
export function newContactBody(draft: NewContactDraft): Record<string, unknown> {
  const body: Record<string, unknown> = { full_name: draft.full_name.trim() };
  for (const key of ["company", "position", "phone", "profile_url"] as const) {
    const value = draft[key].trim();
    if (value) body[key] = value;
  }
  const address = draft.email.trim();
  if (address) {
    const email: ContactEmailInput = { address, email_type: draft.email_type };
    body.emails = [email];
  }
  return body;
}

// ai: claude-opus-5-5 | 2026-11-11 -- phase 2: call-notes import
/** The lines a proposal would add to a lead's summary, labelled. */
export function proposalSummaryItems(proposal: CallNotesProposal): string[] {
  const items = [...proposal.summary_bullets];
  if (proposal.compensation) items.push(`Compensation: ${proposal.compensation}`);
  if (proposal.location) items.push(`Location: ${proposal.location}`);
  if (proposal.interview_steps.length) items.push(`Interview steps: ${proposal.interview_steps.join("; ")}`);
  if (proposal.next_steps.length) items.push(`Next steps: ${proposal.next_steps.join("; ")}`);
  if (proposal.recruiter_requests.length) items.push(`They asked for: ${proposal.recruiter_requests.join("; ")}`);
  return items;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The lead summary with a dated "From call notes" section appended (the server sanitizes it again). */
export function appendedSummaryHtml(current: string | null, proposal: CallNotesProposal, dateLabel: string): string {
  const items = proposalSummaryItems(proposal)
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  return `${current || ""}<h3>From call notes, ${escapeHtml(dateLabel)}</h3><ul>${items}</ul>`;
}
