import React, { useState } from "react";

const SKIP_REASONS = [
  { key: "rejected", label: "Rejected" },
  { key: "duplicate", label: "Duplicate" },
  { key: "req_closed", label: "Req Closed" },
  { key: "not_interested", label: "Not Interested" },
  { key: "other", label: "Other" },
];

export default function JobCard({ job, onAddToSheet, onSkip }) {
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [loading, setLoading] = useState(null); // "sheet" | "skip:<reason>" | "cover" | null
  const [showSkipMenu, setShowSkipMenu] = useState(false);
  const [coverLetter, setCoverLetter] = useState(null);
  const [coverLetterOpen, setCoverLetterOpen] = useState(false);
  const [copied, setCopied] = useState(false);

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

  const handleSkip = async (reason) => {
    setLoading(`skip:${reason}`);
    setShowSkipMenu(false);
    try {
      await onSkip(job.id, reason);
    } finally {
      setLoading(null);
    }
  };

  const handleCoverLetter = async () => {
    if (coverLetter) {
      setCoverLetterOpen((o) => !o);
      return;
    }
    setLoading("cover");
    try {
      const res = await fetch(`/api/jobs/${job.id}/cover-letter`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Generation failed");
      setCoverLetter(data.cover_letter);
      setCoverLetterOpen(true);
    } catch (e) {
      alert("Cover letter generation failed: " + e.message);
    } finally {
      setLoading(null);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(coverLetter || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const busy = loading !== null;

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

      {/* Email origin */}
      {(job.sender_email || job.email_subject) && (
        <div style={emailMetaStyle}>
          {job.email_subject && (
            <div style={{ fontWeight: "500", marginBottom: "2px" }}>{job.email_subject}</div>
          )}
          {job.sender_email && (
            <div style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "2px" }}>
              <span style={{ fontWeight: "500", color: "var(--text)" }}>From: </span>
              {job.sender_email}
            </div>
          )}
        </div>
      )}

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
      <div style={{ marginTop: "12px", display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary"
            style={{ display: "inline-flex", alignItems: "center", padding: "6px 14px", fontSize: "13px", textDecoration: "none" }}
          >
            View Job ↗
          </a>
        )}
        <button
          className="btn-secondary"
          onClick={handleCoverLetter}
          disabled={busy}
          style={{ fontSize: "13px" }}
        >
          {loading === "cover"
            ? "Generating…"
            : coverLetter
            ? coverLetterOpen ? "Hide Cover Letter" : "Show Cover Letter"
            : "Cover Letter"}
        </button>
        <button
          className="btn-success"
          onClick={handleAddToSheet}
          disabled={busy}
        >
          {loading === "sheet" ? "Adding…" : "Add to Sheet"}
        </button>

        {/* Skip with reason picker */}
        <div style={{ position: "relative" }}>
          <button
            className="btn-danger"
            onClick={() => setShowSkipMenu((v) => !v)}
            disabled={busy}
            style={{ display: "flex", alignItems: "center", gap: "5px" }}
          >
            {loading?.startsWith("skip:") ? "Skipping…" : "Skip"}
            {!busy && <span style={{ fontSize: "10px", opacity: 0.7 }}>▾</span>}
          </button>

          {showSkipMenu && (
            <div style={skipMenuStyle}>
              {SKIP_REASONS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => handleSkip(key)}
                  style={skipMenuItemStyle}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  {label}
                </button>
              ))}
              <hr style={{ margin: "4px 0", border: "none", borderTop: "1px solid var(--border)" }} />
              <button
                onClick={() => setShowSkipMenu(false)}
                style={{ ...skipMenuItemStyle, color: "var(--text-muted)", fontSize: "11px" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface2)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Cover letter panel */}
      {coverLetterOpen && coverLetter && (
        <div style={coverLetterPanelStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
            <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Cover Letter
            </span>
            <button
              className="btn-secondary"
              onClick={handleCopy}
              style={{ fontSize: "11px", padding: "3px 10px" }}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <p style={{ fontSize: "13px", lineHeight: "1.7", color: "var(--text)", whiteSpace: "pre-wrap", margin: 0 }}>
            {coverLetter}
          </p>
        </div>
      )}
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

const emailMetaStyle = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "9px 13px",
  fontSize: "12px",
  marginTop: "10px",
  marginBottom: "2px",
};

const coverLetterPanelStyle = {
  marginTop: "14px",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "14px 16px",
};

const skipMenuStyle = {
  position: "absolute",
  bottom: "calc(100% + 4px)",
  left: 0,
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "var(--shadow-sm)",
  minWidth: "130px",
  zIndex: 50,
  padding: "4px",
  display: "flex",
  flexDirection: "column",
};

const skipMenuItemStyle = {
  background: "transparent",
  border: "none",
  padding: "7px 10px",
  fontSize: "13px",
  textAlign: "left",
  cursor: "pointer",
  color: "var(--text)",
  borderRadius: "4px",
  width: "100%",
};
