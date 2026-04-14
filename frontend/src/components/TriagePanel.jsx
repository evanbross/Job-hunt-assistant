import React, { useEffect, useState } from "react";

export default function TriagePanel({ onCountChange }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState("date_desc");

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

  const sortedJobs = [...jobs].sort((a, b) => {
    if (sortBy === "date_desc" || sortBy === "date_asc") {
      const da = new Date(a.date_found || 0);
      const db = new Date(b.date_found || 0);
      return sortBy === "date_desc" ? db - da : da - db;
    }
    // confidence_score: possible_duplicate cards don't have one — sort them last
    const ca = a.confidence_score ?? (a.status === "possible_duplicate" ? -1 : 50);
    const cb = b.confidence_score ?? (b.status === "possible_duplicate" ? -1 : 50);
    return sortBy === "confidence_desc" ? cb - ca : ca - cb;
  });

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
          {triageCount > 0 && `${triageCount} email${triageCount !== 1 ? "s" : ""} need review (low confidence)`}
          {triageCount > 0 && dupeCount > 0 && " · "}
          {dupeCount > 0 && `${dupeCount} possible duplicate${dupeCount !== 1 ? "s" : ""} to confirm`}
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <label style={{ fontSize: "12px", color: "var(--text-muted)" }}>Sort:</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            style={sortSelectStyle}
          >
            <option value="date_desc">Newest first</option>
            <option value="date_asc">Oldest first</option>
            <option value="confidence_desc">Highest confidence</option>
            <option value="confidence_asc">Lowest confidence</option>
          </select>
        </div>
      </div>

      {sortedJobs.map((job) =>
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
            <div style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "2px" }}>
              <span style={{ fontWeight: "500", color: "var(--text)" }}>From: </span>
              {job.sender_email}
            </div>
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
        <span className="badge badge-yellow">
          Low confidence{job.confidence_score != null ? ` · ${job.confidence_score}%` : ""}
        </span>
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
            <div style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "2px" }}>
              <span style={{ fontWeight: "500", color: "var(--text)" }}>From: </span>
              {job.sender_email}
            </div>
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

const sortSelectStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  color: "var(--text)",
  fontSize: "12px",
  padding: "4px 8px",
  cursor: "pointer",
};

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
  marginBottom: "12px",
};

const hintStyle = {
  background: "var(--orange-light)",
  border: "1px solid rgba(194,96,10,0.2)",
  borderRadius: "var(--radius)",
  padding: "9px 13px",
  fontSize: "12px",
  color: "var(--orange)",
  marginBottom: "4px",
};

const snippetStyle = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "10px 13px",
  fontSize: "12px",
  color: "var(--text-muted)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: "120px",
  overflowY: "auto",
  fontFamily: "ui-monospace, 'Cascadia Code', monospace",
  lineHeight: "1.6",
};
