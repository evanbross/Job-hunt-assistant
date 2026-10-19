/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { statusTone } from "../../utils/jobQueueUtils.ts";

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
  const labels: Record<string, string> = {
    applied: "Applied",
    eval_1: "Eval 1",
    eval_2: "Eval 2",
    eval_3: "Eval 3",
    ghosted: "Ghosted",
    job_filled: "Job Filled",
    not_a_fit: "Not a Fit",
    pending_review: "In Job Queue",
    possible_duplicate: "Possible Duplicate",
    rejected: "Rejected",
    skipped: "Skipped",
    triage: "Triage",
    waiting_call: "Waiting for Call",
    waiting_second: "Waiting for 2nd",
  };
  return labels[status] || String(status).replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
}
