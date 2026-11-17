/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { JOB_QUEUE_STATUS_LABELS, statusTone } from "../../utils/jobQueueUtils.ts";

interface StatusBadgeProps {
  /** The status key (e.g., "applied", "rejected"). */
  status?: string | null;
  /** Optional override for the display text. */
  label?: string | null;
  /** Optional override for the color tone (green, yellow, red, orange, gray, accent). */
  tone?: string | null;
}

/**
 * Standard Status Badge component for consistent status rendering across the app.
 */
export default function StatusBadge({ status, label, tone }: StatusBadgeProps) {
  const normalized = String(status || "").trim();
  const badgeTone = tone || statusTone(normalized);
  const displayText = label || statusLabel(normalized);

  return (
    <span className={`badge badge-${badgeTone}`}>
      {displayText}
    </span>
  );
}

function statusLabel(status: string): string {
  // ai: claude-opus-5-5 | 2026-10-27 -- eval_1..3 replaced by the interview
  // and code-eval stages; job-queue labels now come from the shared map.
  // Stage labels mirror backend/db/schema.py's SEED_STAGE_STATUSES for
  // callers that don't load the catalog; pass `label` when you have it.
  const labels: Record<string, string> = {
    ...JOB_QUEUE_STATUS_LABELS,
    // ai: claude-opus-5-5 | 2026-11-11 -- recruiter-lead and offer stages
    recruiter_reached_out: "Recruiter Reached Out",
    recruiter_screen: "Recruiter Screen",
    submitted_to_client: "Submitted to Client",
    offer: "Offer",
    counter_offer: "Counter-offer",
    accepted_offer: "Accepted Offer",
    withdrew: "Withdrew",
    declined_offer: "Declined Offer",
    applied: "Applied",
    code_eval: "Code Eval",
    ghosted: "Ghosted",
    hr_screen_scheduled: "HR Screen Scheduled", // ai: claude-opus-5-5 | 2026-11-17
    hr_screen_done: "HR Screen Done",
    interview_1: "Interview 1", // ai: claude-opus-5-5 | 2026-11-17 -- renumbered from 2..4
    interview_2: "Interview 2",
    interview_3: "Interview 3",
    job_filled: "Job Filled",
    rejected: "Rejected",
    req_closed: "Req Closed",
    waiting_call: "Waiting for Call",
    waiting_second: "Waiting for 2nd",
  };
  return labels[status] || String(status).replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
}
