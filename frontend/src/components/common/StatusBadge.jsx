/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { statusTone } from "../../utils/jobQueueUtils.js";

/**
 * Standard Status Badge component for consistent status rendering across the app.
 *
 * @param {Object} props
 * @param {string} props.status - The status key (e.g., "applied", "rejected").
 * @param {string} [props.label] - Optional override for the display text.
 * @param {string} [props.tone] - Optional override for the color tone (green, yellow, red, orange, gray, accent).
 */
export default function StatusBadge({ status, label, tone }) {
  const normalized = String(status || "").trim();
  const badgeTone = tone || statusTone(normalized);
  const displayText = label || statusLabel(normalized);

  return (
    <span className={`badge badge-${badgeTone}`}>
      {displayText}
    </span>
  );
}

function statusLabel(status) {
  const labels = {
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
