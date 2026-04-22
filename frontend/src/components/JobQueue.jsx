import React, { useCallback, useEffect, useRef, useState } from "react";
import JobCard from "./JobCard.jsx";

export default function JobQueue({ onTriageCountChange, isAuthenticated, onNavigateToEmails }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanState, setScanState] = useState(null);
  const [rescanAllRunning, setRescanAllRunning] = useState(false);
  const [boardSearching, setBoardSearching] = useState(false);
  const [boardState, setBoardState] = useState(null);
  const pollRef = useRef(null);
  const boardPollRef = useRef(null);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs");
      const data = await res.json();
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      /* no-op */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
    return () => {
      clearInterval(pollRef.current);
      clearInterval(boardPollRef.current);
    };
  }, [fetchJobs]);

  const pollScanStatus = async (onDone) => {
    try {
      const res = await fetch("/api/scan/status");
      const state = await res.json();
      setScanState(state);

      if (!state.running) {
        clearInterval(pollRef.current);
        onDone?.();
        await fetchJobs();
        try {
          const t = await fetch("/api/jobs/triage");
          const td = await t.json();
          onTriageCountChange(Array.isArray(td) ? td.length : 0);
        } catch {
          /* no-op */
        }
      }
    } catch {
      clearInterval(pollRef.current);
      onDone?.();
    }
  };

  const startScan = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first (top banner).");
      return;
    }

    const res = await fetch("/api/scan", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start scan");
      return;
    }

    setScanning(true);
    pollRef.current = setInterval(() => pollScanStatus(() => setScanning(false)), 1200);
  };

  const handleRescanAll = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first (top banner).");
      return;
    }
    if (
      !window.confirm(
        "This will delete all email-sourced jobs and re-scan every email from scratch.\n\nBoard-search jobs are kept. Continue?"
      )
    ) {
      return;
    }

    const res = await fetch("/api/emails/rescan-all", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start rescan");
      return;
    }

    setRescanAllRunning(true);
    setScanState(null);
    pollRef.current = setInterval(
      () => pollScanStatus(() => setRescanAllRunning(false)),
      1200
    );
  };

  const handleAddToSheet = async (jobId) => {
    const res = await fetch(`/api/jobs/${jobId}/add-to-sheet`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to add to sheet");
      return;
    }
    setJobs((prev) => prev.filter((j) => j.id !== jobId));
  };

  const handleSkip = async (jobId, reason = "other", feedback = "") => {
    const res = await fetch(`/api/jobs/${jobId}/skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, feedback }),
    });
    if (res.ok) {
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    }
  };

  const startBoardSearch = async () => {
    const res = await fetch("/api/board-search", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start board search");
      return;
    }
    setBoardSearching(true);
    boardPollRef.current = setInterval(pollBoardStatus, 1500);
  };

  const pollBoardStatus = async () => {
    try {
      const res = await fetch("/api/board-search/status");
      const state = await res.json();
      setBoardState(state);
      if (!state.running) {
        clearInterval(boardPollRef.current);
        setBoardSearching(false);
        await fetchJobs();
        try {
          const t = await fetch("/api/jobs/triage");
          const td = await t.json();
          onTriageCountChange(Array.isArray(td) ? td.length : 0);
        } catch { /* no-op */ }
      }
    } catch {
      clearInterval(boardPollRef.current);
      setBoardSearching(false);
    }
  };

  const anyScanRunning = scanning || rescanAllRunning;

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "18px", flexWrap: "wrap" }}>
        <button
          className="btn-primary"
          onClick={startScan}
          disabled={anyScanRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {scanning ? "Scanning…" : "Scan Emails"}
        </button>

        <button
          className="btn-secondary"
          onClick={handleRescanAll}
          disabled={anyScanRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {rescanAllRunning ? "Rescanning All…" : "Rescan All Emails"}
        </button>

        <button
          className="btn-secondary"
          onClick={startBoardSearch}
          disabled={boardSearching}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {boardSearching ? "Searching Boards…" : "Search Job Boards"}
        </button>

        {jobs.length > 0 && (
          <span style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            {jobs.length} job{jobs.length !== 1 ? "s" : ""} ready to review
          </span>
        )}
      </div>

      {/* Scan / rescan-all progress */}
      {anyScanRunning && scanState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{scanState.message}</span>
            <span>{scanState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${scanState.progress}%` }} />
          </div>
          <ErrorList errors={scanState.errors} onNavigateToEmails={onNavigateToEmails} />
        </div>
      )}

      {/* Completed scan summary */}
      {!anyScanRunning && scanState && scanState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{scanState.message}</p>
          <ErrorList errors={scanState.errors} onNavigateToEmails={onNavigateToEmails} />
        </div>
      )}

      {/* Board search progress */}
      {boardSearching && boardState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{boardState.message}</span>
            <span>{boardState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${boardState.progress}%` }} />
          </div>
          {boardState.candidates_found > 0 && (
            <p style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted)" }}>
              {boardState.candidates_processed}/{boardState.candidates_found} scored
              {boardState.jobs_added > 0 && ` · ${boardState.jobs_added} added`}
            </p>
          )}
          {boardState.errors?.length > 0 && (
            <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
              {boardState.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* Completed board search summary */}
      {!boardSearching && boardState && boardState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{boardState.message}</p>
        </div>
      )}

      {/* Job cards */}
      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading jobs…</p>
      ) : jobs.length === 0 ? (
        <div className="empty-state">
          <p style={{ fontSize: "32px" }}>🎯</p>
          <p>No jobs queued. Click "Scan Emails" to check your inbox.</p>
        </div>
      ) : (
        jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            onAddToSheet={handleAddToSheet}
            onSkip={handleSkip}
          />
        ))
      )}
    </div>
  );
}

function ErrorList({ errors, onNavigateToEmails }) {
  if (!errors?.length) return null;
  return (
    <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
      {errors.map((e, i) => {
        const message = typeof e === "string" ? e : e.message;
        const emailId = typeof e === "object" ? e.email_id : null;
        return (
          <li key={i} style={{ marginBottom: "3px" }}>
            {message}
            {emailId && onNavigateToEmails && (
              <button
                onClick={() => onNavigateToEmails(emailId)}
                style={{
                  marginLeft: "8px",
                  fontSize: "10px",
                  padding: "1px 7px",
                  borderRadius: "4px",
                  border: "1px solid var(--red)",
                  background: "transparent",
                  color: "var(--red)",
                  cursor: "pointer",
                  verticalAlign: "middle",
                }}
              >
                View email →
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const progressBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "16px 18px",
  marginBottom: "16px",
  boxShadow: "var(--shadow-sm)",
};
