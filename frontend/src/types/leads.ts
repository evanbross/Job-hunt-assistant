/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Recruiter lead tracking (routes/leads.py). The unions mirror backend/db/schema.py's
// CHECK constraints on interactions, job_contacts and jobs.origin, as routes/leads.py's
// Literal types do.

export type InteractionChannel = "phone" | "video" | "linkedin" | "email" | "text" | "in_person" | "other";
export type InteractionDirection = "inbound" | "outbound";
export type InteractionKind = "call" | "message" | "note" | "transcript";
export type ContactRole =
  | "agency_recruiter"
  | "internal_recruiter"
  | "hiring_manager"
  | "interviewer"
  | "referrer"
  | "other";
export type JobOrigin = "scraped" | "recruiter_contact" | "manual";

/** One row of the interactions table, as every /interactions route returns it. */
export interface Interaction {
  id: number;
  contact_id: number | null;
  job_id: number | null;
  occurred_at: string;
  channel: InteractionChannel;
  direction: InteractionDirection;
  kind: InteractionKind;
  summary: string | null;
  body_html: string | null;
  source: string;
  external_ref: string | null;
  response_received_at: string | null;
  created_at: string;
  /** Set when listed for a job (GET /api/leads/<id>). */
  contact_name?: string | null;
  /** Set when listed for a contact (GET /api/contacts/<id>/activity). */
  job_company?: string | null;
  job_title?: string | null;
}

/** One card on the Leads board (GET /api/leads, LeadService.board). */
export interface LeadSummary {
  id: number;
  company: string | null;
  job_title: string | null;
  url: string | null;
  origin: JobOrigin;
  next_follow_up: string | null;
  date_found: string | null;
  latest_stage_date: string | null;
  primary_contact_id: number | null;
  primary_contact_name: string | null;
  primary_contact_company: string | null;
  last_interaction_at: string | null;
  contact_count: number;
  stage: string;
  is_closed: boolean;
  last_activity_date: string | null;
  idle_days: number | null;
  is_stale: boolean;
}

/** A conversation with someone that isn't tied to a role yet. */
export interface OpenConversation {
  contact_id: number;
  full_name: string;
  company_raw: string;
  position: string;
  recruiter_type: string | null;
  keep_informed: boolean;
  last_interaction_at: string;
  last_channel: InteractionChannel;
  last_summary: string | null;
}

export interface LeadsBoard {
  leads: LeadSummary[];
  open_conversations: OpenConversation[];
  stale_after_days: number;
  open_conversation_window_days: number;
}

/** A person attached to a lead (job_contacts joined to contacts). */
export interface LeadPerson {
  id: number;
  full_name: string;
  company_raw: string;
  position: string;
  phone: string | null;
  email_address: string | null;
  profile_url: string | null;
  recruiter_type: string | null;
  keep_informed: boolean;
  role: ContactRole;
  is_primary: boolean;
}

export interface LeadStage {
  id: number;
  job_id: number;
  status: string;
  date: string;
  notes: string | null;
  created_at: string;
}

/** GET /api/leads/<job_id>. */
export interface LeadDetail {
  lead: {
    id: number;
    company: string | null;
    job_title: string | null;
    url: string | null;
    source: string | null;
    origin: JobOrigin;
    stage: string;
    date_found: string | null;
    next_follow_up: string | null;
    summary_html: string | null;
    job_description: string | null;
    notes: string | null;
  };
  people: LeadPerson[];
  interactions: Interaction[];
  stages: LeadStage[];
}

/** A job a contact is attached to (GET /api/contacts/<id>/activity). */
export interface ContactJob {
  job_id: number;
  company: string | null;
  job_title: string | null;
  origin: JobOrigin;
  role: ContactRole;
  is_primary: boolean;
  stage: string;
}

export interface ContactActivity {
  jobs: ContactJob[];
  interactions: Interaction[];
}

export const CHANNEL_LABELS: Record<InteractionChannel, string> = {
  phone: "Phone",
  video: "Video call",
  linkedin: "LinkedIn",
  email: "Email",
  text: "Text",
  in_person: "In person",
  other: "Other",
};

export const KIND_LABELS: Record<InteractionKind, string> = {
  call: "Call",
  message: "Message",
  note: "Note",
  transcript: "Transcript",
};

export const ROLE_LABELS: Record<ContactRole, string> = {
  agency_recruiter: "Agency recruiter",
  internal_recruiter: "Internal recruiter",
  hiring_manager: "Hiring manager",
  interviewer: "Interviewer",
  referrer: "Referrer",
  other: "Other",
};

export const ORIGIN_LABELS: Record<JobOrigin, string> = {
  scraped: "Found",
  recruiter_contact: "Recruiter",
  manual: "Added by hand",
};
