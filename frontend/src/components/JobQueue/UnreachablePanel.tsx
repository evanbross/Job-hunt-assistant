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

// ai: claude-sonnet-5 | 2026-10-07
import { useState } from "react";
import type { CSSProperties } from "react";
import StatusBadge from "../common/StatusBadge.tsx";
import type { Job } from "../../types/jobs.ts";

const cardStyle: CSSProperties = {
  alignItems: "flex-start",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  display: "flex",
  gap: "16px",
  justifyContent: "space-between",
  marginBottom: "10px",
  padding: "14px 16px",
};

function formatSince(dateStr?: string | null): string {
  if (!dateStr) return "";
  const parsed = new Date(dateStr);
  if (Number.isNaN(parsed.getTime())) return dateStr;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

interface UnreachablePanelProps {
  jobs: Job[];
  loading: boolean;
  onNavigateToJobCard: (id: number | null, status: string) => void;
  onCleared: (jobId: number) => void;
}

// ai: claude-sonnet-5 | 2026-08-09
// Cards the closed-req scan couldn't get a response from, regardless of their
// current pipeline status (queue/triage/possible duplicate/applied) — the
// Job Queue's own search box only ever looks within one status at a time,
// which is why previously-unreachable jobs couldn't be found there.
export default function UnreachablePanel({ jobs, loading, onNavigateToJobCard, onCleared }: UnreachablePanelProps) {
  const [clearingId, setClearingId] = useState<number | null>(null);

  const handleClear = async (jobId: number) => {
    setClearingId(jobId);
    try {
      const res = await fetch(`/api/jobs/${jobId}/clear-unreachable`, { method: "POST" });
      if (!res.ok) {
        window.alert(`Failed to clear flag: ${res.status}`);
        return;
      }
      onCleared(jobId);
    } finally {
      setClearingId(null);
    }
  };

  if (loading) {
    return <p style={{ color: "var(--text-muted)" }}>Loading unreachable jobs…</p>;
  }

  if (jobs.length === 0) {
    return (
      <div className="empty-state">
        <p style={{ fontSize: "32px" }}>🔗</p>
        <p>No jobs are currently flagged unreachable.</p>
      </div>
    );
  }

  return (
    <div>
      {jobs.map((job) => (
        <div key={job.id} style={cardStyle}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: "var(--text)", fontSize: "14px", fontWeight: 600, overflowWrap: "anywhere" }}>
              {job.job_title || "Untitled role"}
              {job.company && <span style={{ color: "var(--text-muted)", fontWeight: 500 }}> at {job.company}</span>}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", marginTop: "6px" }}>
              <StatusBadge status={job.status} />
              {job.source && <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>{job.source}</span>}
              <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>
                Unreachable since {formatSince(job.unreachable_since)}
              </span>
            </div>
            {job.url && (
              <a
                href={job.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--accent)", fontSize: "12px", display: "inline-block", marginTop: "6px", overflowWrap: "anywhere" }}
              >
                {job.url}
              </a>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px", flexShrink: 0 }}>
            <button
              className="btn-secondary"
              onClick={() => onNavigateToJobCard(job.id, job.status || "")}
              style={{ fontSize: "12px", padding: "6px 12px", whiteSpace: "nowrap" }}
            >
              View Card
            </button>
            <button
              className="btn-secondary"
              onClick={() => handleClear(job.id)}
              disabled={clearingId === job.id}
              style={{ fontSize: "12px", padding: "6px 12px", whiteSpace: "nowrap" }}
            >
              {clearingId === job.id ? "Clearing…" : "Clear Flag"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
