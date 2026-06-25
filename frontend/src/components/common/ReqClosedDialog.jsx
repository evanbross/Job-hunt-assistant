/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import Modal from "./Modal.jsx";
import MetricGrid, { MetricItem } from "./MetricGrid.jsx";

const badgeStyle = {
  background: "var(--red-bg, #3a1f1f)",
  borderRadius: "999px",
  color: "var(--red, #f47174)",
  display: "inline-flex",
  alignItems: "center",
  flexShrink: 0,
  fontSize: "12px",
  fontWeight: 700,
  minHeight: "24px",
  padding: "3px 10px",
};

/**
 * Shared result/progress dialog for the "Scan for Closed Reqs" action
 * (LinkedIn + Welcome to the Jungle), used by both the Application Tracking
 * sheet and the Job Queue.
 *
 * @param {Object} props
 * @param {Object} props.result - Status payload from /api/sheet/applications/scan-closed-reqs/status.
 * @param {() => void} props.onClose
 */
// ai: claude-sonnet-4-6 | 2026-06-25
export default function ReqClosedDialog({ result, onClose }) {
  const marked = Array.isArray(result.marked) ? result.marked : [];
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const badUrlErrors = errors.filter((job) => job.reason === "bad_url");
  const unreachableErrors = errors.filter((job) => job.reason !== "bad_url");
  const running = !!result.running;
  const progress = Math.max(0, Math.min(100, result.total ? Math.round(((result.progress ?? 0) / result.total) * 100) : 0));
  const title = running ? "Scanning for Closed Reqs…" : "Scan for Closed Reqs Complete";
  const subtitle = result.error
    ? `Scan failed: ${result.error}`
    : running
      ? (result.message || "Scanning…")
      : (result.message || `Checked ${result.checked ?? 0} job${result.checked === 1 ? "" : "s"}.`);
  const currentJobLine = [result.current_platform, result.current_job_title, result.current_company && `at ${result.current_company}`]
    .filter(Boolean)
    .join(" — ");
  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      footer={<button className="btn-secondary" onClick={onClose}>{running ? "Run in background" : "Close"}</button>}
      maxWidth="640px"
    >
      {running && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>Progress</span>
            <span>{result.progress ?? 0} of {result.total ?? 0}</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${progress}%` }} />
          </div>
          {currentJobLine && (
            <p style={{ margin: "10px 0 0", fontSize: "12px", color: "var(--text-muted)", overflowWrap: "anywhere" }}>
              {currentJobLine}
            </p>
          )}
        </>
      )}

      <div style={{ marginTop: running ? "14px" : "0" }}>
        <MetricGrid>
          <MetricItem label="kept (open)" value={result.kept_count ?? 0} tone={(result.kept_count ?? 0) > 0 ? "green" : "muted"} />
          <MetricItem label="closed" value={result.closed_count ?? marked.length} tone={(result.closed_count ?? marked.length) > 0 ? "accent" : "muted"} />
          <MetricItem label="bad urls" value={result.bad_url_count ?? badUrlErrors.length} tone={(result.bad_url_count ?? badUrlErrors.length) > 0 ? "yellow" : "muted"} />
          <MetricItem label="unreachable" value={result.unreachable_count ?? unreachableErrors.length} tone={(result.unreachable_count ?? unreachableErrors.length) > 0 ? "red" : "muted"} />
        </MetricGrid>
      </div>

      {marked.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "420px", overflowY: "auto", marginTop: "14px" }}>
          {marked.map((job) => (
            <div
              key={job.id}
              style={{
                alignItems: "center",
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                display: "flex",
                gap: "12px",
                justifyContent: "space-between",
                padding: "10px 12px",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ color: "var(--text)", fontSize: "13px", fontWeight: 600, overflowWrap: "anywhere" }}>
                  {job.job_title || "Untitled role"}
                  {job.company && <span style={{ color: "var(--text-muted)", fontWeight: 500 }}> at {job.company}</span>}
                  {job.platform && <span style={{ color: "var(--text-muted)", fontWeight: 500 }}> · {job.platform}</span>}
                </div>
              </div>
              <span style={badgeStyle}>Req Closed</span>
            </div>
          ))}
        </div>
      )}
      {errors.length > 0 && (
        <p style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "12px" }}>
          {badUrlErrors.length > 0 && (
            <>Bad URL: {badUrlErrors.map((job) => job.job_title || job.company || `#${job.id}`).join(", ")}<br /></>
          )}
          {unreachableErrors.length > 0 && (
            <>Unreachable: {unreachableErrors.map((job) => job.job_title || job.company || `#${job.id}`).join(", ")}</>
          )}
        </p>
      )}
    </Modal>
  );
}
