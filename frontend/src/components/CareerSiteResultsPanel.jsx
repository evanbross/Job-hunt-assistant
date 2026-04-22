import React, { useEffect, useRef, useState } from "react";

function scoreBadge(score) {
  if (score == null) return <span className="badge badge-gray">—</span>;
  const cls = score >= 70 ? "badge-green" : score >= 40 ? "badge-yellow" : "badge-red";
  return <span className={`badge ${cls}`}>{score}</span>;
}

function getMatchLevel(score) {
  if (score >= 85) return { key: "ideal", label: "Ideal Match" };
  if (score >= 70) return { key: "high", label: "High Match" };
  if (score >= 40) return { key: "medium", label: "Medium Match" };
  return { key: "low", label: "Low Match" };
}

function confidenceBadge(level, score) {
  const label = `${level || "unknown"}${score != null ? ` (${score})` : ""}`;
  if (level === "high") return <span className="badge badge-green">{label}</span>;
  if (level === "medium") return <span className="badge badge-yellow">{label}</span>;
  if (level === "low") return <span className="badge badge-red">{label}</span>;
  return <span className="badge badge-gray">{label}</span>;
}

function formatTimestampLocal(value) {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

export default function CareerSiteResultsPanel() {
  const [rows, setRows] = useState([]);
  const [failures, setFailures] = useState([]);
  const [sites, setSites] = useState([]);
  const [showSiteForm, setShowSiteForm] = useState(false);
  const [siteForm, setSiteForm] = useState({ name: "", url: "", enabled: true });
  const [editingSiteId, setEditingSiteId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [selectedFailure, setSelectedFailure] = useState(null);
  const [selectedJobId, setSelectedJobId] = useState(null);
  const pollRef = useRef(null);

  const fetchRows = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/career-scan/results");
      const data = await res.json();
      const normalizedRows = Array.isArray(data) ? [...data] : [];
      normalizedRows.sort((a, b) => {
        const aScore = typeof a?.match_score === "number" ? a.match_score : -1;
        const bScore = typeof b?.match_score === "number" ? b.match_score : -1;
        return bScore - aScore;
      });
      setRows(normalizedRows);
    } finally {
      setLoading(false);
    }
  };

  const fetchStatus = async () => {
    const res = await fetch("/api/career-scan/status");
    const data = await res.json();
    setStatus(data);
    setRunning(Boolean(data.running));
    return data;
  };

  const fetchFailures = async () => {
    const res = await fetch("/api/career-scan/failures?limit=25");
    const data = await res.json();
    setFailures(Array.isArray(data) ? data : []);
  };

  const fetchSites = async () => {
    const res = await fetch("/api/career-sites");
    const data = await res.json();
    setSites(Array.isArray(data) ? data : []);
  };

  useEffect(() => {
    fetchRows();
    fetchFailures();
    fetchSites();
    fetchStatus().then((s) => {
      if (s.running) {
        pollRef.current = setInterval(pollStatus, 1500);
      }
    });
    return () => clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    if (!rows.length) {
      setSelectedJobId(null);
      return;
    }
    const selectedStillExists = rows.some((r) => r.id === selectedJobId);
    if (!selectedStillExists) {
      setSelectedJobId(rows[0].id);
    }
  }, [rows, selectedJobId]);

  const pollStatus = async () => {
    try {
      const s = await fetchStatus();
      if (!s.running) {
        clearInterval(pollRef.current);
        await fetchRows();
        await fetchFailures();
      }
    } catch {
      clearInterval(pollRef.current);
      setRunning(false);
    }
  };

  const runScanNow = async () => {
    const res = await fetch("/api/career-scan/run", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start career-site scan");
      return;
    }
    setRunning(true);
    pollRef.current = setInterval(pollStatus, 1500);
  };

  const runScanForSite = async (siteId) => {
    const res = await fetch(`/api/career-sites/${siteId}/scan`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start site rescan");
      return;
    }
    setRunning(true);
    pollRef.current = setInterval(pollStatus, 1500);
  };

  const clearResults = async () => {
    if (!window.confirm("Delete all career-site scan results?")) return;
    const res = await fetch("/api/career-scan/results", { method: "DELETE" });
    if (!res.ok) {
      alert("Failed to clear results");
      return;
    }
    await fetchRows();
  };

  const startCreateSite = () => {
    setEditingSiteId(null);
    setSiteForm({ name: "", url: "", enabled: true });
    setShowSiteForm(true);
  };

  const startEditSite = (site) => {
    setEditingSiteId(site.id);
    setSiteForm({ name: site.name || "", url: site.url || "", enabled: !!site.enabled });
    setShowSiteForm(true);
  };

  const submitSiteForm = async () => {
    if (!siteForm.name.trim() || !siteForm.url.trim()) return;
    const endpoint = editingSiteId ? `/api/career-sites/${editingSiteId}` : "/api/career-sites";
    const method = editingSiteId ? "PUT" : "POST";
    const res = await fetch(endpoint, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: siteForm.name.trim(),
        url: siteForm.url.trim(),
        enabled: !!siteForm.enabled,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to save site");
      return;
    }
    setShowSiteForm(false);
    setEditingSiteId(null);
    setSiteForm({ name: "", url: "", enabled: true });
    await fetchSites();
  };

  const deleteSite = async (siteId) => {
    if (!window.confirm("Delete this career site?")) return;
    const res = await fetch(`/api/career-sites/${siteId}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to delete site");
      return;
    }
    await fetchSites();
  };

  const toggleSiteEnabled = async (site) => {
    const res = await fetch(`/api/career-sites/${site.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !site.enabled }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to update site");
      return;
    }
    await fetchSites();
  };

  const selectedJob = rows.find((r) => r.id === selectedJobId) || null;
  const groupedRows = groupRowsBySiteAndMatch(rows);

  return (
    <div>
      <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "14px", flexWrap: "wrap" }}>
        <button className="btn-primary" onClick={runScanNow} disabled={running}>
          {running ? "Scanning…" : "Run Scan Now"}
        </button>
        <button className="btn-secondary" onClick={fetchRows}>Refresh</button>
        <button className="btn-danger" onClick={clearResults}>Clear Results</button>
        {status && (
          <span style={{ color: "var(--text-muted)", fontSize: "12px", marginLeft: "auto" }}>
            Manual scan mode
            {status.last_run_at ? ` · Last run: ${formatTimestampLocal(status.last_run_at)}` : ""}
          </span>
        )}
      </div>

      <div style={{ background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)", marginBottom: "14px" }}>
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <strong style={{ fontSize: "12px" }}>Career Sites</strong>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: "12px" }} onClick={fetchSites}>Refresh Sites</button>
            <button className="btn-primary" style={{ padding: "4px 10px", fontSize: "12px" }} onClick={startCreateSite}>Add Site</button>
          </div>
        </div>

        {showSiteForm && (
          <div style={{ padding: "12px", borderBottom: "1px solid var(--border)", display: "grid", gap: "10px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "10px" }}>
              <input
                value={siteForm.name}
                onChange={(e) => setSiteForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="Site Name (e.g. Acme Careers)"
              />
              <input
                value={siteForm.url}
                onChange={(e) => setSiteForm((prev) => ({ ...prev, url: e.target.value }))}
                placeholder="https://company.com/careers"
              />
            </div>
            <label style={{ display: "flex", gap: "6px", alignItems: "center", fontSize: "12px", color: "var(--text-muted)" }}>
              <input
                type="checkbox"
                checked={!!siteForm.enabled}
                onChange={(e) => setSiteForm((prev) => ({ ...prev, enabled: e.target.checked }))}
              />
              Enabled
            </label>
            <div style={{ display: "flex", gap: "8px" }}>
              <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "12px" }} onClick={submitSiteForm}>
                {editingSiteId ? "Save Site" : "Add Site"}
              </button>
              <button
                className="btn-secondary"
                style={{ padding: "4px 12px", fontSize: "12px" }}
                onClick={() => {
                  setShowSiteForm(false);
                  setEditingSiteId(null);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {sites.length === 0 ? (
          <p style={{ margin: 0, padding: "12px", color: "var(--text-muted)", fontSize: "12px" }}>No sites configured yet.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["Name", "URL", "Platform", "Enabled", "Actions"].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sites.map((site) => (
                  <tr key={site.id}>
                    <td style={tdStyle}>{site.name || "—"}</td>
                    <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: "11px", whiteSpace: "normal" }}>{site.url || "—"}</td>
                    <td style={tdStyle}>{site.detected_platform || "—"}</td>
                    <td style={tdStyle}>
                      <button className="btn-secondary" style={{ padding: "2px 9px", fontSize: "11px" }} onClick={() => toggleSiteEnabled(site)}>
                        {site.enabled ? "On" : "Off"}
                      </button>
                    </td>
                    <td style={tdStyle}>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                        <button
                          className="btn-primary"
                          style={{ padding: "2px 9px", fontSize: "11px" }}
                          onClick={() => runScanForSite(site.id)}
                          disabled={running}
                        >
                          Rescan
                        </button>
                        <button className="btn-secondary" style={{ padding: "2px 9px", fontSize: "11px" }} onClick={() => startEditSite(site)}>Edit</button>
                        <button className="btn-danger" style={{ padding: "2px 9px", fontSize: "11px" }} onClick={() => deleteSite(site.id)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {running && status && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{status.message}</span>
            <span>{status.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${status.progress}%` }} />
          </div>
          <p style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted)" }}>
            Sites scanned: {status.sites_scanned} · Found: {status.jobs_found} · New: {status.jobs_added}
          </p>
          {status.errors?.length > 0 && (
            <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
              {status.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading results…</p>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <p style={{ fontSize: "32px" }}>🌐</p>
          <p>No career-site scan results yet.</p>
        </div>
      ) : (
        <div style={{ background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)", marginBottom: "14px", padding: "12px" }}>
          <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: "10px" }}>Extracted Jobs</div>
          <div style={{ display: "grid", gap: "10px" }}>
            {groupedRows.map((siteGroup) => (
              <details key={siteGroup.siteKey} open style={treeGroupStyle}>
                <summary style={treeSiteSummaryStyle}>
                  <span>{siteGroup.siteLabel}</span>
                  <span style={treeCountStyle}>{siteGroup.jobs.length} job{siteGroup.jobs.length === 1 ? "" : "s"}</span>
                </summary>
                <div style={treeChildrenStyle}>
                  {siteGroup.matchGroups.map((matchGroup) => (
                    <details key={`${siteGroup.siteKey}-${matchGroup.key}`} open style={treeSubgroupStyle}>
                      <summary style={treeMatchSummaryStyle}>
                        <span>{matchGroup.label}</span>
                        <span style={treeCountStyle}>{matchGroup.jobs.length}</span>
                      </summary>
                      <div style={treeChildrenStyle}>
                        {matchGroup.jobs.map((row) => (
                          <div
                            key={row.id}
                            onClick={() => setSelectedJobId(row.id)}
                            style={{
                              ...treeJobRowStyle,
                              background: row.id === selectedJobId ? "var(--accent-light)" : "var(--surface2)",
                              borderColor: row.id === selectedJobId ? "rgba(74,92,247,0.35)" : "var(--border)",
                            }}
                          >
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                                {row.job_title || "—"}
                              </div>
                              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                                {row.tracking_status || "New"}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
                              {scoreBadge(row.match_score)}
                              {confidenceBadge(row.confidence_level, row.confidence_score)}
                              {row.job_url ? (
                                <a href={row.job_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                                  Open
                                </a>
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>—</span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </details>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </div>
      )}

      <div style={{ background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)", marginBottom: "14px" }}>
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
          <strong style={{ fontSize: "12px" }}>Job Details</strong>
        </div>
        {!selectedJob ? (
          <p style={{ margin: 0, padding: "12px", color: "var(--text-muted)", fontSize: "12px" }}>
            Select a job row above to view details.
          </p>
        ) : (
          <div style={{ padding: "12px" }}>
            <div style={{ fontSize: "15px", fontWeight: 600, marginBottom: "8px" }}>
              {selectedJob.job_title
                ? `${selectedJob.job_title} at ${selectedJob.site_name || selectedJob.site_url || "—"}`
                : selectedJob.site_name || selectedJob.site_url || "—"}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "10px", marginBottom: "10px" }}>
              <DetailCell label="Tracking" value={selectedJob.tracking_status || "New"} />
              <DetailCell label="Match Score" value={selectedJob.match_score ?? "—"} />
              <DetailCell label="Confidence" value={`${selectedJob.confidence_level || "unknown"}${selectedJob.confidence_score != null ? ` (${selectedJob.confidence_score})` : ""}`} />
              <DetailCell label="Date Scanned" value={formatTimestampLocal(selectedJob.scanned_at)} />
              <DetailCell label="Date Posted" value={selectedJob.posted_at || "—"} />
              <DetailCell label="Tracking Source" value={selectedJob.tracking_source || "—"} />
            </div>
            <div style={{ marginBottom: "10px" }}>
              <div style={detailLabelStyle}>Job URL</div>
              <div style={{ fontFamily: "monospace", fontSize: "12px", wordBreak: "break-all" }}>
                {selectedJob.job_url ? <a href={selectedJob.job_url} target="_blank" rel="noreferrer"> {selectedJob.job_url}</a> : "—"}
              </div>
            </div>
            <div style={{ marginBottom: "10px" }}>
              <div style={detailLabelStyle}>Match Reasoning</div>
              <div style={detailValueBlockStyle}>{selectedJob.match_reasoning || "—"}</div>
            </div>
            <div>
              <div style={detailLabelStyle}>Description Snippet</div>
              <div style={detailValueBlockStyle}>{selectedJob.job_text || "—"}</div>
            </div>
          </div>
        )}
      </div>

      <div style={{ background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)" }}>
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: "12px" }}>Recent Scan Failures</strong>
          <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: "12px" }} onClick={fetchFailures}>
            Refresh Failures
          </button>
        </div>
        {failures.length === 0 ? (
          <p style={{ margin: 0, padding: "12px", color: "var(--text-muted)", fontSize: "12px" }}>
            No failures logged.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["When", "Platform", "Site", "Error"].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {failures.map((f) => (
                  <tr key={f.id}>
                    <td style={tdStyle}>{formatTimestampLocal(f.occurred_at)}</td>
                    <td style={tdStyle}>{f.platform || "—"}</td>
                    <td style={tdStyle}>{f.site_name || f.site_url || "—"}</td>
                    <td style={{ ...tdStyle, whiteSpace: "normal", maxWidth: "520px" }}>
                      <div style={{ display: "flex", gap: "8px", alignItems: "flex-start", justifyContent: "space-between" }}>
                        <span>{f.error_message || "—"}</span>
                        <button
                          className="btn-secondary"
                          style={{ padding: "2px 9px", fontSize: "11px", flexShrink: 0 }}
                          onClick={() => setSelectedFailure(f)}
                        >
                          Details
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedFailure && (
        <FailureDetailsModal
          failure={selectedFailure}
          onClose={() => setSelectedFailure(null)}
        />
      )}
    </div>
  );
}

function FailureDetailsModal({ failure, onClose }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-lg)",
          width: "100%",
          maxWidth: "760px",
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
          <strong style={{ fontSize: "13px" }}>Failure Details</strong>
          <button className="btn-secondary" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={onClose}>
            Close
          </button>
        </div>
        <div style={{ padding: "12px 14px", overflowY: "auto" }}>
          <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "max-content 1fr", rowGap: "6px", columnGap: "12px", fontSize: "12px" }}>
            <dt style={modalDtStyle}>When</dt>
            <dd style={modalDdStyle}>{formatTimestampLocal(failure.occurred_at)}</dd>
            <dt style={modalDtStyle}>Platform</dt>
            <dd style={modalDdStyle}>{failure.platform || "—"}</dd>
            <dt style={modalDtStyle}>Trigger</dt>
            <dd style={modalDdStyle}>{failure.trigger || "—"}</dd>
            <dt style={modalDtStyle}>Site Name</dt>
            <dd style={modalDdStyle}>{failure.site_name || "—"}</dd>
            <dt style={modalDtStyle}>Site URL</dt>
            <dd style={{ ...modalDdStyle, fontFamily: "monospace", wordBreak: "break-all" }}>{failure.site_url || "—"}</dd>
            <dt style={modalDtStyle}>Site Source ID</dt>
            <dd style={modalDdStyle}>{failure.site_source_id ?? "—"}</dd>
            <dt style={modalDtStyle}>Failure ID</dt>
            <dd style={modalDdStyle}>{failure.id ?? "—"}</dd>
            <dt style={modalDtStyle}>Error</dt>
            <dd style={modalDdStyle}>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace", fontSize: "12px", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "10px" }}>
                {failure.error_message || "—"}
              </pre>
            </dd>
          </dl>
        </div>
      </div>
    </div>
  );
}

function DetailCell({ label, value }) {
  return (
    <div>
      <div style={detailLabelStyle}>{label}</div>
      <div style={{ fontSize: "13px" }}>{value ?? "—"}</div>
    </div>
  );
}

function groupRowsBySiteAndMatch(rows) {
  const siteMap = new Map();

  rows.forEach((row) => {
    const siteLabel = row.site_name || row.site_url || "Unknown Site";
    const siteKey = row.site_name || row.site_url || `unknown-${row.id}`;
    const matchLevel = getMatchLevel(row.match_score);

    if (!siteMap.has(siteKey)) {
        siteMap.set(siteKey, {
          siteKey,
          siteLabel,
          jobs: [],
          matchGroups: {
            ideal: { key: "ideal", label: "Ideal Match", jobs: [] },
            high: { key: "high", label: "High Match", jobs: [] },
            medium: { key: "medium", label: "Medium Match", jobs: [] },
            low: { key: "low", label: "Low Match", jobs: [] },
          },
        });
    }

    const siteGroup = siteMap.get(siteKey);
    siteGroup.jobs.push(row);
    siteGroup.matchGroups[matchLevel.key].jobs.push(row);
  });

  return Array.from(siteMap.values()).map((siteGroup) => ({
    siteKey: siteGroup.siteKey,
    siteLabel: siteGroup.siteLabel,
    jobs: siteGroup.jobs,
    matchGroups: ["ideal", "high", "medium", "low"]
      .map((key) => siteGroup.matchGroups[key])
      .filter((group) => group.jobs.length > 0),
  }));
}

const tdStyle = {
  padding: "10px 12px",
  borderBottom: "1px solid var(--border)",
  color: "var(--text)",
  verticalAlign: "top",
  fontSize: "12px",
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
  whiteSpace: "nowrap",
};

const progressBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "16px 18px",
  marginBottom: "16px",
  boxShadow: "var(--shadow-sm)",
};

const modalDtStyle = {
  color: "var(--text-muted)",
  fontWeight: 600,
};

const modalDdStyle = {
  margin: 0,
  color: "var(--text)",
};

const detailLabelStyle = {
  fontSize: "11px",
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: "3px",
};

const detailValueBlockStyle = {
  fontSize: "12px",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "8px 10px",
};

const treeGroupStyle = {
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  background: "var(--surface2)",
};

const treeSubgroupStyle = {
  borderLeft: "2px solid var(--border)",
  paddingLeft: "10px",
};

const treeSiteSummaryStyle = {
  cursor: "pointer",
  listStyle: "none",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "10px",
  padding: "10px 12px",
  fontSize: "13px",
  fontWeight: 700,
};

const treeMatchSummaryStyle = {
  cursor: "pointer",
  listStyle: "none",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "10px",
  padding: "8px 0",
  fontSize: "12px",
  fontWeight: 600,
};

const treeChildrenStyle = {
  display: "grid",
  gap: "8px",
  padding: "0 12px 12px 12px",
};

const treeCountStyle = {
  fontSize: "11px",
  color: "var(--text-muted)",
  fontWeight: 500,
};

const treeJobRowStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "10px",
  padding: "10px 12px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  cursor: "pointer",
};
