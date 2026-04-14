import React, { useCallback, useEffect, useRef, useState } from "react";
import JobCard from "./JobCard.jsx";

export default function JobQueue({ onTriageCountChange, isAuthenticated }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanState, setScanState] = useState(null);
  const pollRef = useRef(null);

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
    return () => clearInterval(pollRef.current);
  }, [fetchJobs]);

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
    pollRef.current = setInterval(pollScanStatus, 1200);
  };

  const pollScanStatus = async () => {
    try {
      const res = await fetch("/api/scan/status");
      const state = await res.json();
      setScanState(state);

      if (!state.running) {
        clearInterval(pollRef.current);
        setScanning(false);
        // Refresh jobs and triage count
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
      setScanning(false);
    }
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

  const handleSkip = async (jobId) => {
    const res = await fetch(`/api/jobs/${jobId}/skip`, { method: "POST" });
    if (res.ok) {
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    }
  };

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "18px" }}>
        <button
          className="btn-primary"
          onClick={startScan}
          disabled={scanning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {scanning ? "Scanning…" : "Scan Emails"}
        </button>

        {jobs.length > 0 && (
          <span style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            {jobs.length} job{jobs.length !== 1 ? "s" : ""} ready to review
          </span>
        )}
      </div>

      {/* Scan progress */}
      {scanning && scanState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{scanState.message}</span>
            <span>{scanState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${scanState.progress}%` }} />
          </div>
          {scanState.errors?.length > 0 && (
            <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
              {scanState.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* Completed scan summary */}
      {!scanning && scanState && scanState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{scanState.message}</p>
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

const progressBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "14px 16px",
  marginBottom: "16px",
};
