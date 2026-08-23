/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useEffect, useState } from "react";

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
 * Shared dialog for the "Scan for Closed Reqs" action, used by both the
 * Application Tracking sheet and the Job Queue.
 *
 * Opens in a setup step first (`result.setup` truthy): checkboxes to pick which
 * career sites to scan, fetched from /api/applications/scan-closed-reqs/platforms
 * so the list can't drift from the backend. The caller's `onStart(platforms)`
 * kicks off the scan and swaps `result` to the status payload, which renders the
 * existing progress/result view.
 *
 * @param {Object} props
 * @param {Object} props.result - `{ setup: true }` for the setup step, otherwise
 *   the status payload from /api/applications/scan-closed-reqs/status.
 * @param {(platforms: string[]) => void} props.onStart
 * @param {() => void} props.onClose
 */
// ai: claude-fable-5 | 2026-07-16
export default function ReqClosedDialog({ result, onStart, onClose }) {
  const isSetup = !!result.setup;
  const [platformOptions, setPlatformOptions] = useState(null);
  const [selectedPlatforms, setSelectedPlatforms] = useState([]);

  useEffect(() => {
    if (!isSetup) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/applications/scan-closed-reqs/platforms");
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !Array.isArray(data)) return;
        setPlatformOptions(data);
        setSelectedPlatforms(data.map((p) => p.key));
      } catch {
        /* options stay null; the dialog keeps showing the loading line */
      }
    })();
    return () => { cancelled = true; };
  }, [isSetup]);

  const togglePlatform = (key) => {
    setSelectedPlatforms((previous) =>
      previous.includes(key) ? previous.filter((k) => k !== key) : [...previous, key]
    );
  };

  if (isSetup) {
    return (
      <Modal
        title="Scan for Closed Reqs"
        subtitle="Choose which career sites to scan."
        onClose={onClose}
        footer={
          <>
            <button className="btn-secondary" onClick={onClose}>Cancel</button>
            <button
              className="btn-primary"
              disabled={selectedPlatforms.length === 0}
              onClick={() => onStart(selectedPlatforms)}
            >
              Start Scan
            </button>
          </>
        }
        maxWidth="480px"
      >
        {platformOptions === null ? (
          <p style={{ margin: 0, fontSize: "13px", color: "var(--text-muted)" }}>
            Loading career sites…
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {platformOptions.map((platform) => (
              <label
                key={platform.key}
                style={{
                  alignItems: "center",
                  background: "var(--surface2)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  cursor: "pointer",
                  display: "flex",
                  fontSize: "13px",
                  gap: "10px",
                  padding: "10px 12px",
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedPlatforms.includes(platform.key)}
                  onChange={() => togglePlatform(platform.key)}
                />
                {platform.label}
              </label>
            ))}
          </div>
        )}
      </Modal>
    );
  }

  const marked = Array.isArray(result.marked) ? result.marked : [];
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const badUrlErrors = errors.filter((job) => job.reason === "bad_url");
  const unreachableErrors = errors.filter((job) => job.reason !== "bad_url");
  const running = !!result.running;
  // ai: claude-fable-5 | 2026-07-17 — in-scan recheck progress, fed by the scan
  // thread's batch loop (browser_checks_*) rather than the raw queue snapshot,
  // which also counts done rows from earlier runs.
  const browserChecksTotal = result.browser_checks_total ?? 0;
  const browserChecksDone = result.browser_checks_done ?? 0;
  const browserRecheckPhase = running && browserChecksTotal > 0;
  const browserQueue = result.browser_queue || {};
  const browserRemaining = browserQueue.remaining ?? 0;
  const browserDone = browserQueue.done ?? 0;
  const browserFailed = browserQueue.failed ?? 0;
  const browserTotal = browserRemaining + browserDone + browserFailed;
  const browserDraining = !running && browserRemaining > 0;
  const browserEtaMinutes = browserQueue.eta_seconds ? Math.ceil(browserQueue.eta_seconds / 60) : 0;
  const progress = Math.max(0, Math.min(100, result.total ? Math.round(((result.progress ?? 0) / result.total) * 100) : 0));
  const title = running
    ? (browserRecheckPhase ? "Rechecking via Browser Extension…" : "Scanning for Closed Reqs…") // ai: claude-fable-5 | 2026-07-17
    : browserDraining
      ? "Waiting on Authenticated Browser Rechecks…"
      : "Scan for Closed Reqs Complete";
  const subtitle = result.error
    ? `Scan failed: ${result.error}`
    : running
      ? (result.message || "Scanning…")
      : browserDraining
        ? `${browserRemaining} job${browserRemaining === 1 ? "" : "s"} still awaiting an authenticated recheck via the browser extension.`
        : (result.message || `Checked ${result.checked ?? 0} job${result.checked === 1 ? "" : "s"}.`);
  const currentJobLine = [result.current_platform, result.current_job_title, result.current_company && `at ${result.current_company}`]
    .filter(Boolean)
    .join(" — ");
  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      footer={<button className="btn-secondary" onClick={onClose}>{running || browserDraining ? "Run in background" : "Close"}</button>}
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

      {/* ai: claude-fable-5 | 2026-07-17 — while the scan runs, recheck progress
          comes from the scan thread's batch loop; the raw queue snapshot below is
          only shown after the scan exits with rechecks still queued (extension
          offline), since its totals include done rows from earlier runs. */}
      {browserRecheckPhase && (
        <div style={{ marginTop: "14px", padding: "10px 12px", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>Authenticated recheck (browser extension)</span>
            <span>{browserChecksDone} of {browserChecksTotal} done</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${Math.round((browserChecksDone / browserChecksTotal) * 100)}%` }} />
          </div>
        </div>
      )}

      {!running && browserTotal > 0 && (
        <div style={{ marginTop: "14px", padding: "10px 12px", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>Authenticated recheck (browser extension)</span>
            <span>{browserDone} of {browserTotal} done</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${Math.round((browserDone / browserTotal) * 100)}%` }} />
          </div>
          <p style={{ margin: "8px 0 0", fontSize: "12px", color: "var(--text-muted)" }}>
            {browserRemaining > 0
              ? `${browserRemaining} still queued — about ${browserEtaMinutes} min remaining (requires the browser extension to be running).`
              : browserFailed > 0
                ? `Finished — ${browserFailed} failed after repeated retries.`
                : "Finished."}
          </p>
        </div>
      )}

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
