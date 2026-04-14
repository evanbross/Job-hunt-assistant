import React, { useEffect, useState } from "react";
import JobQueue from "./components/JobQueue.jsx";
import TriagePanel from "./components/TriagePanel.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";

const TABS = ["Job Queue", "Triage", "History", "Settings"];

const styles = {
  app: {
    display: "flex",
    flexDirection: "column",
    minHeight: "100vh",
  },
  header: {
    background: "var(--surface)",
    borderBottom: "1px solid var(--border)",
    padding: "0 24px",
    display: "flex",
    alignItems: "center",
    gap: "24px",
    height: "52px",
    flexShrink: 0,
  },
  logo: {
    fontSize: "16px",
    fontWeight: "700",
    color: "var(--text)",
    marginRight: "8px",
  },
  tabBar: {
    display: "flex",
    gap: "4px",
  },
  tab: (active) => ({
    padding: "6px 16px",
    borderRadius: "var(--radius)",
    background: active ? "var(--accent)" : "transparent",
    color: active ? "#fff" : "var(--text-muted)",
    border: "none",
    cursor: "pointer",
    fontSize: "13px",
    fontWeight: active ? "600" : "400",
    display: "flex",
    alignItems: "center",
    gap: "6px",
    transition: "background 0.15s, color 0.15s",
  }),
  countBadge: {
    background: "var(--red)",
    color: "#fff",
    borderRadius: "999px",
    padding: "1px 6px",
    fontSize: "10px",
    fontWeight: "700",
  },
  authBanner: {
    background: "rgba(108,99,255,0.12)",
    border: "1px solid rgba(108,99,255,0.3)",
    borderRadius: "var(--radius)",
    padding: "10px 16px",
    margin: "16px 24px 0",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "12px",
  },
  content: {
    flex: 1,
    padding: "20px 24px",
    maxWidth: "1200px",
    width: "100%",
    margin: "0 auto",
  },
};

export default function App() {
  const [activeTab, setActiveTab] = useState(0);
  const [triageCount, setTriageCount] = useState(0);
  const [authStatus, setAuthStatus] = useState(null); // null | true | false

  useEffect(() => {
    fetchAuthStatus();
    fetchTriageCount();

    // Check if returning from OAuth flow
    const params = new URLSearchParams(window.location.search);
    if (params.get("auth") === "success") {
      window.history.replaceState({}, "", "/");
      fetchAuthStatus();
    }
  }, []);

  const fetchAuthStatus = async () => {
    try {
      const res = await fetch("/api/auth/status");
      const data = await res.json();
      setAuthStatus(data.authenticated);
    } catch {
      setAuthStatus(false);
    }
  };

  const fetchTriageCount = async () => {
    try {
      const res = await fetch("/api/jobs/triage");
      const data = await res.json();
      setTriageCount(Array.isArray(data) ? data.length : 0);
    } catch {
      setTriageCount(0);
    }
  };

  const handleAuthClick = async () => {
    try {
      const res = await fetch("/api/auth/google");
      const data = await res.json();
      if (data.error) {
        alert(data.error);
        return;
      }
      window.location.href = data.auth_url;
    } catch (e) {
      alert("Failed to start auth: " + e.message);
    }
  };

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <span style={styles.logo}>💼 Job Hunt Assistant</span>
        <nav style={styles.tabBar}>
          {TABS.map((tab, i) => (
            <button
              key={tab}
              style={styles.tab(activeTab === i)}
              onClick={() => {
                setActiveTab(i);
                if (i === 1) fetchTriageCount();
              }}
            >
              {tab}
              {i === 1 && triageCount > 0 && (
                <span style={styles.countBadge}>{triageCount}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {authStatus === false && (
        <div style={styles.authBanner}>
          <span style={{ color: "var(--accent)", fontSize: "13px" }}>
            Google account not connected — scanning and sheet export are disabled.
          </span>
          <button className="btn-primary" style={{ flexShrink: 0 }} onClick={handleAuthClick}>
            Connect Google Account
          </button>
        </div>
      )}

      <main style={styles.content}>
        {activeTab === 0 && (
          <JobQueue
            onTriageCountChange={setTriageCount}
            isAuthenticated={authStatus === true}
          />
        )}
        {activeTab === 1 && (
          <TriagePanel onCountChange={setTriageCount} />
        )}
        {activeTab === 2 && <HistoryTable />}
        {activeTab === 3 && <SettingsPanel />}
      </main>
    </div>
  );
}

// ── History table (inline — simple enough not to need its own file) ───────────

function HistoryTable() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/jobs/history")
      .then((r) => r.json())
      .then((data) => {
        setJobs(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ color: "var(--text-muted)" }}>Loading...</p>;

  if (jobs.length === 0) {
    return (
      <div className="empty-state">
        <p style={{ fontSize: "32px" }}>📋</p>
        <p>No history yet.</p>
      </div>
    );
  }

  const tdStyle = {
    padding: "10px 12px",
    borderBottom: "1px solid var(--border)",
    color: "var(--text)",
    verticalAlign: "middle",
  };
  const thStyle = {
    ...tdStyle,
    color: "var(--text-muted)",
    fontWeight: "600",
    fontSize: "11px",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    background: "var(--surface)",
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {["Date", "Company", "Title", "Source", "Score", "Status"].map((h) => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} style={{ background: "var(--surface)" }}>
              <td style={tdStyle}>{job.date_found || "—"}</td>
              <td style={tdStyle}>{job.company || "—"}</td>
              <td style={tdStyle}>
                {job.url ? (
                  <a href={job.url} target="_blank" rel="noreferrer">{job.job_title || "—"}</a>
                ) : (
                  job.job_title || "—"
                )}
              </td>
              <td style={tdStyle}>{job.source || "—"}</td>
              <td style={tdStyle}>
                <ScoreBadge score={job.fit_score} />
              </td>
              <td style={tdStyle}>
                <span
                  className={`badge ${job.status === "added_to_sheet" ? "badge-green" : "badge-gray"}`}
                >
                  {job.status === "added_to_sheet" ? "Added" : "Skipped"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScoreBadge({ score }) {
  if (score === null || score === undefined) {
    return <span className="badge badge-gray">—</span>;
  }
  const cls = score >= 70 ? "badge-green" : score >= 40 ? "badge-yellow" : "badge-red";
  return <span className={`badge ${cls}`}>{score}</span>;
}
