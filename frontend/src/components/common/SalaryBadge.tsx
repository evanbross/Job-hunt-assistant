/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { salaryRangeForJob } from "../../utils/salaryUtils.ts";
import type { Job } from "../../types/jobs.ts";

// ai: claude-sonnet-5 | 2026-07-26
// LinkedIn gates its offsite "Apply" URL behind a sign-in modal, so the fit
// scorer can only see a salary range when LinkedIn itself displays one on the
// listing (rare) — a missing range there doesn't mean the employer's actual
// application page has none, just that we couldn't reach it. Flag that
// specifically for LinkedIn instead of silently showing nothing, so the user
// knows to check the original posting rather than assume no salary exists.
const _SOURCES_WITH_KNOWN_ACCESS_GAP = new Set(["linkedin"]);

interface SalaryBadgeProps {
  job: Pick<Job, "salary_min" | "salary_max" | "raw_jd_snippet" | "fit_reasoning" | "source">;
}

export default function SalaryBadge({ job }: SalaryBadgeProps) {
  const salaryRange = salaryRangeForJob(job);
  if (salaryRange) {
    return <span className="badge badge-green">Salary: {salaryRange}</span>;
  }
  const source = String(job.source || "").trim().toLowerCase();
  if (_SOURCES_WITH_KNOWN_ACCESS_GAP.has(source)) {
    return (
      <span
        className="badge badge-yellow"
        title="LinkedIn hides the application page's salary behind a sign-in wall — check the original posting's Apply link directly."
      >
        Salary not verified
      </span>
    );
  }
  return null;
}
