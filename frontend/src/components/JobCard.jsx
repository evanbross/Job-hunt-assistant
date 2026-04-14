import React, { useState } from "react";

export default function JobCard({ job, onAddToSheet, onSkip }) {
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [loading, setLoading] = useState(null); // "sheet" | "skip" | null

  const score = job.fit_score;
  const scoreCls =
    score === null || score === undefined
      ? "badge-gray"
      : score >= 70
      ? "badge-green"
      : score >= 40
      ? "badge-yellow"
      : "badge-red";

  const handleAddToSheet = async () => {
    setLoading("sheet");
    try {
      await onAddToSheet(job.id);
    } finally {
      setLoading(null);
    }
  };

  const handleSkip = async () => {
    setLoading("skip");
    try {
      await onSkip(job.id);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div style={cardStyle}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
        <span className={`badge ${scoreCls}`} style={{ fontSize: "14px", padding: "4px 10px", flexShrink: 0 }}>
          {score !== null && score !== undefined ? score : "—"}
        </span>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "15px", fontWeight: "600", color: "var(--text)", marginBottom: "2px" }}>
            {job.job_title || <em style={{ color: "var(--text-muted)" }}>Unknown Title</em>}
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            {job.company || <em>Unknown Company</em>}
          </div>
        </div>

        <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
          <span className="badge badge-accent">{job.source || "?"}</span>
          <span className="badge badge-gray">{job.date_found || "—"}</span>
        </div>
      </div>

      {/* Reasoning (collapsible) */}
      {job.fit_reasoning && (
        <div style={{ marginTop: "10px" }}>
          <button
            className="btn-secondary"
            style={{ fontSize: "12px", padding: "4px 10px" }}
            onClick={() => setReasoningOpen((o) => !o)}
          >
            {reasoningOpen ? "Hide reasoning" : "Show reasoning"}
          </button>
          {reasoningOpen && (
            <p style={{ marginTop: "8px", color: "var(--text-muted)", fontSize: "13px", lineHeight: "1.6" }}>
              {job.fit_reasoning}
            </p>
          )}
        </div>
      )}

      {/* Action row */}
      <div style={{ marginTop: "12px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            className="btn-primary"
            style={{ display: "inline-flex", alignItems: "center", padding: "6px 14px", borderRadius: "var(--radius)", color: "#fff", background: "var(--accent)", fontSize: "13px" }}
          >
            Apply ↗
          </a>
        )}
        <button
          className="btn-success"
          onClick={handleAddToSheet}
          disabled={loading !== null}
        >
          {loading === "sheet" ? "Adding…" : "Add to Sheet"}
        </button>
        <button
          className="btn-danger"
          onClick={handleSkip}
          disabled={loading !== null}
        >
          {loading === "skip" ? "Skipping…" : "Skip"}
        </button>
      </div>
    </div>
  );
}

const cardStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "18px",
  marginBottom: "12px",
  boxShadow: "var(--shadow-sm)",
  transition: "box-shadow 0.2s, border-color 0.2s",
};
