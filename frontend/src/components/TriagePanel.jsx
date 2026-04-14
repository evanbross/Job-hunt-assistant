import React, { useEffect, useState } from "react";

export default function TriagePanel({ onCountChange }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchTriage = async () => {
    try {
      const res = await fetch("/api/jobs/triage");
      const data = await res.json();
      const list = Array.isArray(data) ? data : [];
      setJobs(list);
      onCountChange(list.length);
    } catch {
      /* no-op */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTriage();
  }, []);

  const handleApprove = async (job) => {
    const senderEmail = extractSender(job.raw_email_snippet, job);
    const subjectPattern = extractSubjectWords(job.job_title || "");

    const res = await fetch(`/api/jobs/${job.id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender_email: senderEmail, subject_pattern: subjectPattern }),
    });

    if (res.ok) {
      const updated = jobs.filter((j) => j.id !== job.id);
      setJobs(updated);
      onCountChange(updated.length);
    } else {
      const err = await res.json();
      alert(err.error || "Failed to approve");
    }
  };

  const handleReject = async (job) => {
    const senderEmail = extractSender(job.raw_email_snippet, job);
    const subjectPattern = extractSubjectWords(job.job_title || "");

    const res = await fetch(`/api/jobs/${job.id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender_email: senderEmail, subject_pattern: subjectPattern }),
    });

    if (res.ok) {
      const updated = jobs.filter((j) => j.id !== job.id);
      setJobs(updated);
      onCountChange(updated.length);
    } else {
      const err = await res.json();
      alert(err.error || "Failed to reject");
    }
  };

  if (loading) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  if (jobs.length === 0) {
    return (
      <div className="empty-state">
        <p style={{ fontSize: "32px" }}>✅</p>
        <p>No emails waiting for review.</p>
        <p>Low-confidence emails from your next scan will appear here.</p>
      </div>
    );
  }

  const dupeCount = jobs.filter((j) => j.status === "possible_duplicate").length;
  const triageCount = jobs.length - dupeCount;

  return (
    <div>
      <p style={{ color: "var(--text-muted)", marginBottom: "16px", fontSize: "13px" }}>
        {triageCount > 0 && `${triageCount} email${triageCount !== 1 ? "s" : ""} need review (low confidence)`}
        {triageCount > 0 && dupeCount > 0 && " · "}
        {dupeCount > 0 && `${dupeCount} possible duplicate${dupeCount !== 1 ? "s" : ""} to confirm`}
      </p>

      {jobs.map((job) =>
        job.status === "possible_duplicate" ? (
          <DuplicateCard
            key={job.id}
            job={job}
            onSameJob={() => handleReject(job)}
            onDifferentJob={() => handleApprove(job)}
          />
        ) : (
          <TriageCard
            key={job.id}
            job={job}
            onApprove={() => handleApprove(job)}
            onReject={() => handleReject(job)}
          />
        )
      )}
    </div>
  );
}

function DuplicateCard({ job, onSameJob, onDifferentJob }) {
  const [loadingAction, setLoadingAction] = useState(null);

  const act = async (fn, label) => {
    setLoadingAction(label);
    try {
      await fn();
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <div style={{ ...cardStyle, borderColor: "var(--warning, #b45309)" }}>
      {/* Meta */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "8px" }}>
        <span className="badge badge-orange">Possible duplicate</span>
        {job.source && <span className="badge badge-accent">{job.source}</span>}
        {job.date_found && <span className="badge badge-gray">{job.date_found}</span>}
      </div>

      {/* Email origin */}
      {(job.sender_email || job.email_subject) && (
        <div style={emailMetaStyle}>
          {job.email_subject && (
            <div style={{ fontWeight: "500", marginBottom: "2px" }}>{job.email_subject}</div>
          )}
          {job.sender_email && (
            <div style={{ color: "var(--text-muted)", fontSize: "11px" }}>{job.sender_email}</div>
          )}
        </div>
      )}

      {/* Job details */}
      <div style={{ marginBottom: "10px" }}>
        {job.company && (
          <div style={{ fontSize: "15px", fontWeight: "600" }}>{job.company}</div>
        )}
        {job.job_title && (
          <div style={{ color: "var(--text-muted)", fontSize: "13px" }}>{job.job_title}</div>
        )}
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: "12px", wordBreak: "break-all" }}
          >
            {job.url}
          </a>
        )}
      </div>

      {/* Duplicate hint */}
      {job.duplicate_hint && (
        <div style={hintStyle}>{job.duplicate_hint}</div>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
        <button
          className="btn-danger"
          onClick={() => act(onSameJob, "same")}
          disabled={loadingAction !== null}
          style={{ flex: 1 }}
        >
          {loadingAction === "same" ? "Removing…" : "Yes, same job — skip it"}
        </button>
        <button
          className="btn-success"
          onClick={() => act(onDifferentJob, "different")}
          disabled={loadingAction !== null}
          style={{ flex: 1 }}
        >
          {loadingAction === "different" ? "Moving…" : "No, it's a different role"}
        </button>
      </div>
    </div>
  );
}

function TriageCard({ job, onApprove, onReject }) {
  const [loadingAction, setLoadingAction] = useState(null);

  const act = async (fn, label) => {
    setLoadingAction(label);
    try {
      await fn();
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <div style={cardStyle}>
      {/* Meta */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "8px" }}>
        <span className="badge badge-yellow">Low confidence</span>
        {job.source && <span className="badge badge-accent">{job.source}</span>}
        {job.date_found && <span className="badge badge-gray">{job.date_found}</span>}
      </div>

      {/* Email origin */}
      {(job.sender_email || job.email_subject) && (
        <div style={emailMetaStyle}>
          {job.email_subject && (
            <div style={{ fontWeight: "500", marginBottom: "2px" }}>{job.email_subject}</div>
          )}
          {job.sender_email && (
            <div style={{ color: "var(--text-muted)", fontSize: "11px" }}>{job.sender_email}</div>
          )}
        </div>
      )}

      {/* AI guesses */}
      <div style={{ marginBottom: "10px" }}>
        {job.company && (
          <div style={{ fontSize: "15px", fontWeight: "600" }}>{job.company}</div>
        )}
        {job.job_title && (
          <div style={{ color: "var(--text-muted)", fontSize: "13px" }}>{job.job_title}</div>
        )}
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: "12px", wordBreak: "break-all" }}
          >
            {job.url}
          </a>
        )}
        {!job.company && !job.job_title && (
          <span style={{ color: "var(--text-muted)", fontSize: "13px", fontStyle: "italic" }}>
            No job details detected
          </span>
        )}
      </div>

      {/* Snippet */}
      {job.raw_email_snippet && (
        <pre style={snippetStyle}>{job.raw_email_snippet}</pre>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
        <button
          className="btn-success"
          onClick={() => act(onApprove, "approve")}
          disabled={loadingAction !== null}
          style={{ flex: 1 }}
        >
          {loadingAction === "approve" ? "Moving…" : "It's a Job Posting"}
        </button>
        <button
          className="btn-danger"
          onClick={() => act(onReject, "reject")}
          disabled={loadingAction !== null}
          style={{ flex: 1 }}
        >
          {loadingAction === "reject" ? "Removing…" : "Not Relevant"}
        </button>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractSender(snippet, job) {
  // The sender email is stored in the raw_email_snippet indirectly;
  // we pass the source/domain as a fallback for the feedback pattern.
  // In a real scenario the server would store sender separately — this is best-effort.
  return job.source ? `unknown@${job.source.toLowerCase().replace(/\s+/g, "")}.com` : "";
}

function extractSubjectWords(text) {
  return text
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 3)
    .slice(0, 6)
    .join(" ");
}

const cardStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "16px",
  marginBottom: "12px",
};

const emailMetaStyle = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "8px 12px",
  fontSize: "12px",
  marginBottom: "10px",
};

const hintStyle = {
  background: "rgba(180, 83, 9, 0.1)",
  border: "1px solid rgba(180, 83, 9, 0.35)",
  borderRadius: "var(--radius)",
  padding: "8px 12px",
  fontSize: "12px",
  color: "var(--text-muted)",
  marginBottom: "4px",
};

const snippetStyle = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "10px 12px",
  fontSize: "12px",
  color: "var(--text-muted)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: "120px",
  overflowY: "auto",
  fontFamily: "monospace",
};
