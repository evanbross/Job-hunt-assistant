import React, { useCallback, useEffect, useRef, useState } from "react";

const DISPOSITION_INFO = {
  job_created:       { label: "Job extracted",      cls: "badge-accent" },
  noise:             { label: "Not a job email",    cls: "badge-gray" },
  ack:               { label: "Application ack",    cls: "badge-gray" },
  duplicate:         { label: "Duplicate email",    cls: "badge-gray" },
  feedback_filtered: { label: "Filtered (feedback)",cls: "badge-gray" },
  parser_error:      { label: "Parser error",       cls: "badge-red" },
};

const JOB_STATUS_INFO = {
  pending_review:     { label: "Pending review",    cls: "badge-yellow" },
  triage:             { label: "Triage",             cls: "badge-gray" },
  possible_duplicate: { label: "Possible duplicate", cls: "badge-gray" },
  added_to_sheet:     { label: "Added to sheet",    cls: "badge-green" },
  skipped:            { label: "Skipped",            cls: "badge-gray" },
};

const SKIP_REASON_LABELS = {
  rejected:       "Rejected",
  duplicate:      "Duplicate",
  req_closed:     "Req Closed",
  not_interested: "Not Interested",
  other:          "Other",
};

function statusBadge(email) {
  if (email.job_status) {
    return JOB_STATUS_INFO[email.job_status] || { label: email.job_status, cls: "badge-gray" };
  }
  return DISPOSITION_INFO[email.disposition] || { label: email.disposition || "Unknown", cls: "badge-gray" };
}

function statusSortKey(email) {
  return statusBadge(email).label;
}

// ── Raw body modal ────────────────────────────────────────────────────────────

function RawBodyModal({ email, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.55)", display: "flex",
        alignItems: "center", justifyContent: "center", padding: "24px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-lg)",
          width: "100%", maxWidth: "760px", maxHeight: "80vh",
          display: "flex", flexDirection: "column", overflow: "hidden",
        }}
      >
        {/* Modal header */}
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "12px" }}>
          <div>
            <div style={{ fontSize: "13px", fontWeight: "600", color: "var(--text)", marginBottom: "2px" }}>
              {email.email_subject || <em style={{ color: "var(--text-muted)" }}>No subject</em>}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{email.sender_email || "—"}</div>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: "18px", color: "var(--text-muted)", lineHeight: 1, flexShrink: 0, padding: "2px 6px" }}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        {/* Modal body */}
        <div style={{ padding: "16px 20px", overflowY: "auto", flex: 1 }}>
          {/* Error message */}
          {email.parser_error_msg ? (
            <div style={{ marginBottom: "16px" }}>
              <div style={{ fontSize: "11px", fontWeight: "700", textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--red, #e05252)", marginBottom: "6px" }}>
                Error
              </div>
              <pre style={{
                fontFamily: "monospace", fontSize: "12px", color: "var(--red, #e05252)",
                whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0,
                background: "rgba(224,82,82,0.07)", borderRadius: "var(--radius)",
                padding: "10px 14px", border: "1px solid rgba(224,82,82,0.25)",
              }}>
                {email.parser_error_msg}
              </pre>
            </div>
          ) : (
            <div style={{ marginBottom: "16px", fontSize: "12px", color: "var(--text-muted)", fontStyle: "italic" }}>
              No error message stored — this email was scanned before error capture was enabled.
            </div>
          )}

          {/* Raw body */}
          <div style={{ fontSize: "11px", fontWeight: "700", textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--text-muted)", marginBottom: "10px" }}>
            Raw Email Body
          </div>
          {email.raw_body ? (
            <pre style={{
              fontFamily: "monospace", fontSize: "12px", color: "var(--text)",
              whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0,
              background: "var(--surface2)", borderRadius: "var(--radius)",
              padding: "12px 14px", border: "1px solid var(--border)",
            }}>
              {email.raw_body}
            </pre>
          ) : (
            <p style={{ fontSize: "13px", color: "var(--text-muted)" }}>
              Email body not stored — this email was scanned before body capture was enabled.
              Use <strong>Rescan This Email</strong> to re-process it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Detail panel shown when a row is selected ─────────────────────────────────

function DetailPanel({ email, rescanning, onRescan, onShowBody, onSetHidden }) {
  const { label, cls } = statusBadge(email);
  const isRescanning = rescanning[email.email_id];
  const isParserError = email.disposition === "parser_error";

  return (
    <div style={detailPanelStyle}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", marginBottom: "16px" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "13px", fontWeight: "600", color: "var(--text)", marginBottom: "3px", wordBreak: "break-word" }}>
            {email.email_subject || <em style={{ color: "var(--text-muted)" }}>No subject</em>}
          </div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{email.sender_email || "—"}</div>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexShrink: 0 }}>
          {isParserError ? (
            <button
              onClick={onShowBody}
              className={`badge ${cls}`}
              style={{ cursor: "pointer", background: "none", border: "none", padding: 0, textDecoration: "underline dotted" }}
              title="Click to view raw email body"
            >
              {label}
            </button>
          ) : (
            <span className={`badge ${cls}`}>{label}</span>
          )}
          {email.job_status === "skipped" && email.skip_reason && (
            <span className="badge badge-gray" style={{ textTransform: "capitalize" }}>
              {SKIP_REASON_LABELS[email.skip_reason] || email.skip_reason}
            </span>
          )}
        </div>
      </div>

      {/* Two-column grid for metadata + job info */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
        {/* Left: email metadata */}
        <div>
          <div style={sectionLabel}>Email</div>
          <dl style={dlStyle}>
            <dt style={dtStyle}>Date</dt>
            <dd style={ddStyle}>{email.email_date || "—"}</dd>
            <dt style={dtStyle}>Scanned at</dt>
            <dd style={ddStyle}>{email.scanned_at ? email.scanned_at.replace("T", " ").slice(0, 19) : "—"}</dd>
            <dt style={dtStyle}>Disposition</dt>
            <dd style={ddStyle}>
              {(DISPOSITION_INFO[email.disposition] || { label: email.disposition || "—" }).label}
            </dd>
            <dt style={dtStyle}>Message ID</dt>
            <dd style={{ ...ddStyle, fontFamily: "monospace", fontSize: "11px", wordBreak: "break-all", color: "var(--text-muted)" }}>
              {email.email_id}
            </dd>
          </dl>
        </div>

        {/* Right: job info (if any) */}
        <div>
          <div style={sectionLabel}>Job</div>
          {email.job_id ? (
            <dl style={dlStyle}>
              <dt style={dtStyle}>Title</dt>
              <dd style={ddStyle}>{email.job_title || "—"}</dd>
              <dt style={dtStyle}>Company</dt>
              <dd style={ddStyle}>{email.company || "—"}</dd>
              <dt style={dtStyle}>Status</dt>
              <dd style={ddStyle}>
                {JOB_STATUS_INFO[email.job_status]?.label || email.job_status || "—"}
                {email.skip_reason && (
                  <span style={{ color: "var(--text-muted)", marginLeft: "6px" }}>
                    ({SKIP_REASON_LABELS[email.skip_reason] || email.skip_reason})
                  </span>
                )}
              </dd>
              {email.fit_score != null && (
                <>
                  <dt style={dtStyle}>Fit score</dt>
                  <dd style={ddStyle}>{email.fit_score}</dd>
                </>
              )}
              {email.job_count > 1 && (
                <>
                  <dt style={dtStyle}>Jobs in email</dt>
                  <dd style={ddStyle}>{email.job_count}</dd>
                </>
              )}
            </dl>
          ) : (
            <p style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
              No job record linked to this email.
            </p>
          )}
        </div>
      </div>

      {/* Actions */}
      <div style={{ marginTop: "16px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
        <button
          className="btn-secondary"
          onClick={() => onRescan(email.email_id)}
          disabled={isRescanning}
          style={{ fontSize: "13px" }}
        >
          {isRescanning ? "Rescanning…" : "Rescan This Email"}
        </button>
        {isParserError && (
          <button
            className="btn-secondary"
            onClick={onShowBody}
            style={{ fontSize: "13px" }}
          >
            View Raw Email
          </button>
        )}
        <button
          className="btn-secondary"
          onClick={() => onSetHidden(email.email_id, !email.hidden)}
          style={{ fontSize: "13px", marginLeft: "auto" }}
        >
          {email.hidden ? "Unhide" : "Hide"}
        </button>
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function EmailsPanel({ isAuthenticated, onTriageCountChange, focusEmailId }) {
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(null);
  const [rescanning, setRescanning] = useState({});
  const [rescanAllRunning, setRescanAllRunning] = useState(false);
  const [scanState, setScanState] = useState(null);
  const [sortCol, setSortCol] = useState("date");
  const [sortDir, setSortDir] = useState("desc");
  const [showHidden, setShowHidden] = useState(false);
  const [errorModalEmail, setErrorModalEmail] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (focusEmailId) {
      setSelected(focusEmailId);
      setFilter("all");
    }
  }, [focusEmailId]);

  const fetchEmails = useCallback(async (activeFilter) => {
    setLoading(true);
    try {
      const q = activeFilter && activeFilter !== "all" ? `?filter=${activeFilter}` : "";
      const res = await fetch(`/api/emails${q}`);
      const data = await res.json();
      setEmails(Array.isArray(data) ? data : []);
    } catch {
      /* no-op */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEmails(filter);
    return () => clearInterval(pollRef.current);
  }, [filter, fetchEmails]);

  const selectedEmail = emails.find((e) => e.email_id === selected) ?? null;

  const handleColSort = (col) => {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const visibleEmails = showHidden ? emails : emails.filter((e) => !e.hidden);

  const sortedEmails = [...visibleEmails].sort((a, b) => {
    let cmp = 0;
    if (sortCol === "date") {
      const da = a.email_date || a.scanned_at || "";
      const db = b.email_date || b.scanned_at || "";
      cmp = da < db ? -1 : da > db ? 1 : 0;
    } else if (sortCol === "status") {
      const sa = statusSortKey(a);
      const sb = statusSortKey(b);
      cmp = sa.localeCompare(sb);
    }
    return sortDir === "asc" ? cmp : -cmp;
  });

  const handleRowClick = (emailId) => {
    setSelected((prev) => (prev === emailId ? null : emailId));
  };

  const handleRescan = async (emailId) => {
    if (!isAuthenticated) {
      alert("Connect your Google account first.");
      return;
    }
    setRescanning((prev) => ({ ...prev, [emailId]: true }));
    try {
      const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}/rescan`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || "Rescan failed");
        return;
      }
      await fetchEmails(filter);
      try {
        const t = await fetch("/api/jobs/triage");
        const td = await t.json();
        onTriageCountChange?.(Array.isArray(td) ? td.length : 0);
      } catch { /* no-op */ }
    } catch (e) {
      alert("Rescan failed: " + e.message);
    } finally {
      setRescanning((prev) => ({ ...prev, [emailId]: false }));
    }
  };

  const handleRescanAll = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first.");
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
    setSelected(null);
    setScanState(null);
    setEmails([]);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch("/api/scan/status");
        const state = await r.json();
        setScanState(state);
        if (!state.running) {
          clearInterval(pollRef.current);
          setRescanAllRunning(false);
          await fetchEmails(filter);
          try {
            const t = await fetch("/api/jobs/triage");
            const td = await t.json();
            onTriageCountChange?.(Array.isArray(td) ? td.length : 0);
          } catch { /* no-op */ }
        }
      } catch {
        clearInterval(pollRef.current);
        setRescanAllRunning(false);
      }
    }, 1200);
  };

  const handleSetHidden = async (emailId, hidden) => {
    await fetch(`/api/emails/${encodeURIComponent(emailId)}/hide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden }),
    });
    await fetchEmails(filter);
    if (hidden) setSelected(null);
  };

  const counts = emails.reduce(
    (acc, e) => {
      if (e.job_status && ["pending_review", "triage", "possible_duplicate"].includes(e.job_status)) acc.potential++;
      else if (e.job_status && ["added_to_sheet", "skipped"].includes(e.job_status)) acc.acted++;
      return acc;
    },
    { potential: 0, acted: 0 }
  );

  const filterButtons = [
    { key: "all", label: "All" },
    { key: "potential", label: `Potential Jobs${filter !== "potential" && counts.potential ? ` (${counts.potential})` : ""}` },
    { key: "acted",     label: `Acted On${filter !== "acted" && counts.acted ? ` (${counts.acted})` : ""}` },
  ];

  const SortIndicator = ({ col }) => {
    if (sortCol !== col) return <span style={{ opacity: 0.3, marginLeft: "4px" }}>↕</span>;
    return <span style={{ marginLeft: "4px" }}>{sortDir === "asc" ? "↑" : "↓"}</span>;
  };

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "18px", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: "4px" }}>
          {filterButtons.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              style={{
                padding: "6px 14px",
                fontSize: "12px",
                borderRadius: "var(--radius)",
                border: "1px solid var(--border)",
                background: filter === key ? "var(--accent)" : "var(--surface)",
                color: filter === key ? "#fff" : "var(--text)",
                cursor: "pointer",
                fontWeight: filter === key ? "600" : "400",
                transition: "background 0.15s, color 0.15s",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", color: "var(--text-muted)", cursor: "pointer", userSelect: "none" }}>
          <input
            type="checkbox"
            checked={showHidden}
            onChange={(e) => setShowHidden(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          Show hidden
        </label>
        <div style={{ marginLeft: "auto" }}>
          <button
            className="btn-secondary"
            onClick={handleRescanAll}
            disabled={rescanAllRunning}
            style={{ padding: "6px 16px", fontSize: "13px" }}
          >
            {rescanAllRunning ? "Rescanning All…" : "Rescan All Emails"}
          </button>
        </div>
      </div>

      {/* Rescan-all progress */}
      {rescanAllRunning && scanState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{scanState.message}</span>
            <span>{scanState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${scanState.progress}%` }} />
          </div>
        </div>
      )}
      {!rescanAllRunning && scanState && scanState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{scanState.message}</p>
        </div>
      )}

      {/* Email list */}
      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading emails…</p>
      ) : emails.length === 0 ? (
        <div className="empty-state">
          <p style={{ fontSize: "32px" }}>📬</p>
          <p>No emails match this filter.</p>
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto", overflowY: "auto", maxHeight: "50vh", background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th
                    style={{ ...thStyle, position: "sticky", top: 0, zIndex: 1, cursor: "pointer", userSelect: "none" }}
                    onClick={() => handleColSort("date")}
                  >
                    Date <SortIndicator col="date" />
                  </th>
                  <th style={{ ...thStyle, position: "sticky", top: 0, zIndex: 1 }}>Sender</th>
                  <th style={{ ...thStyle, position: "sticky", top: 0, zIndex: 1 }}>Subject</th>
                  <th
                    style={{ ...thStyle, position: "sticky", top: 0, zIndex: 1, cursor: "pointer", userSelect: "none" }}
                    onClick={() => handleColSort("status")}
                  >
                    Status <SortIndicator col="status" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedEmails.map((email) => {
                  const isSelected = email.email_id === selected;
                  const { label, cls } = statusBadge(email);
                  const isParserError = email.disposition === "parser_error";
                  const isHidden = Boolean(email.hidden);
                  return (
                    <tr
                      key={email.email_id}
                      onClick={() => handleRowClick(email.email_id)}
                      style={{
                        background: isSelected ? "var(--accent-light)" : "var(--surface)",
                        cursor: "pointer",
                        outline: isSelected ? "2px solid var(--accent)" : "none",
                        outlineOffset: "-2px",
                        transition: "background 0.1s",
                        opacity: isHidden ? 0.45 : 1,
                      }}
                      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--surface2)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = isSelected ? "var(--accent-light)" : "var(--surface)"; }}
                    >
                      <td style={{ ...tdStyle, whiteSpace: "nowrap", fontSize: "12px", color: "var(--text-muted)" }}>
                        {email.email_date || "—"}
                      </td>
                      <td style={{ ...tdStyle, maxWidth: "180px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "12px" }}>
                        {email.sender_email || "—"}
                      </td>
                      <td style={{ ...tdStyle, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {email.email_subject || "—"}
                      </td>
                      <td style={{ ...tdStyle, whiteSpace: "nowrap" }}>
                        {isParserError ? (
                          <button
                            className={`badge ${cls}`}
                            onClick={(e) => { e.stopPropagation(); setErrorModalEmail(email); }}
                            style={{ cursor: "pointer", background: "none", border: "none", padding: 0, textDecoration: "underline dotted" }}
                            title="Click to view raw email body"
                          >
                            {label}
                          </button>
                        ) : (
                          <span className={`badge ${cls}`}>{label}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "8px" }}>
            {visibleEmails.length} email{visibleEmails.length !== 1 ? "s" : ""}
            {!showHidden && emails.filter((e) => e.hidden).length > 0 && (
              <span style={{ marginLeft: "6px" }}>
                · {emails.filter((e) => e.hidden).length} hidden
              </span>
            )}
          </p>
        </>
      )}

      {/* Details panel below the list */}
      <div style={{ marginTop: "16px" }}>
        <div style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "8px" }}>
          Details
        </div>
        {selectedEmail ? (
          <DetailPanel
            email={selectedEmail}
            rescanning={rescanning}
            onRescan={handleRescan}
            onShowBody={() => setErrorModalEmail(selectedEmail)}
            onSetHidden={handleSetHidden}
          />
        ) : (
          <div style={{ ...detailPanelStyle, display: "flex", alignItems: "center", justifyContent: "center", minHeight: "80px" }}>
            <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>Select a row to see details.</p>
          </div>
        )}
      </div>

      {/* Parser error body modal */}
      {errorModalEmail && (
        <RawBodyModal
          email={errorModalEmail}
          onClose={() => setErrorModalEmail(null)}
        />
      )}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const tdStyle = {
  padding: "10px 14px",
  borderBottom: "1px solid var(--border)",
  color: "var(--text)",
  verticalAlign: "middle",
  fontSize: "13px",
};

const thStyle = {
  ...tdStyle,
  color: "var(--text-muted)",
  fontWeight: "600",
  fontSize: "11px",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  background: "var(--surface2)",
  borderBottom: "1.5px solid var(--border)",
};

const progressBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "16px 18px",
  marginBottom: "16px",
  boxShadow: "var(--shadow-sm)",
};

const detailPanelStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "20px",
  boxShadow: "var(--shadow-sm)",
};

const sectionLabel = {
  fontSize: "10px",
  fontWeight: "700",
  textTransform: "uppercase",
  letterSpacing: "0.07em",
  color: "var(--text-muted)",
  marginBottom: "8px",
};

const dlStyle = {
  display: "grid",
  gridTemplateColumns: "max-content 1fr",
  columnGap: "16px",
  rowGap: "5px",
  margin: 0,
};

const dtStyle = {
  fontSize: "12px",
  color: "var(--text-muted)",
  fontWeight: "500",
  whiteSpace: "nowrap",
  paddingTop: "1px",
};

const ddStyle = {
  fontSize: "13px",
  color: "var(--text)",
  margin: 0,
  wordBreak: "break-word",
};
