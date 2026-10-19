/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-14
import type { Job } from "../../types/jobs.ts";

interface LinkedInInsightBadgeProps {
  job: Pick<Job, "linkedin_ai_insight">;
}

const _MAX_LABEL_LENGTH = 48;

// ai: claude-sonnet-5 | 2026-10-14
// linkedin_ai_insight is captured verbatim from LinkedIn's own "AI-powered job
// assessment" text (e.g. "You'd be a top applicant, based on your skills,
// experience, and chances of hearing back"), which varies in wording across
// jobs/users -- truncate for the compact badge label rather than pattern-
// matching specific phrases, and keep the full sentence as the hover title.
export default function LinkedInInsightBadge({ job }: LinkedInInsightBadgeProps) {
  const insight = (job.linkedin_ai_insight || "").trim();
  if (!insight) return null;
  const label =
    insight.length > _MAX_LABEL_LENGTH ? `${insight.slice(0, _MAX_LABEL_LENGTH - 1)}…` : insight;
  return (
    <span className="badge badge-accent" title={insight}>
      ✨ {label}
    </span>
  );
}
