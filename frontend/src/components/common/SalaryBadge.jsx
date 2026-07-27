/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 * You may read, study, and run this code for personal purposes.
 * You may NOT:
 *   - Use this code, in whole or in part, in any commercial product or service
 *   - Sell, sublicense, or otherwise monetize this code or derivatives of it
 *   - Redistribute this code without explicit written permission from ZombieTech LLC
 *
 * No warranty is provided, express or implied. Use at your own risk.
 *
 * Licensing inquiries: license@zombietech.biz
 */

import { salaryRangeForJob } from "../../utils/salaryUtils.js";

// ai: claude-sonnet-5 | 2026-07-26
// LinkedIn gates its offsite "Apply" URL behind a sign-in modal, so the fit
// scorer can only see a salary range when LinkedIn itself displays one on the
// listing (rare) — a missing range there doesn't mean the employer's actual
// application page has none, just that we couldn't reach it. Flag that
// specifically for LinkedIn instead of silently showing nothing, so the user
// knows to check the original posting rather than assume no salary exists.
const _SOURCES_WITH_KNOWN_ACCESS_GAP = new Set(["linkedin"]);

export default function SalaryBadge({ job }) {
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
