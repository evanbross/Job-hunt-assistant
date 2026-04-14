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
  headerWrap: {
    background: "linear-gradient(135deg, var(--header-from) 0%, var(--header-to) 100%)",
    boxShadow: "0 3px 14px rgba(45,58,158,0.35)",
    flexShrink: 0,
    position: "sticky",
    top: 0,
    zIndex: 100,
  },
  header: {
    padding: "0 28px",
    display: "flex",
    alignItems: "center",
    gap: "28px",
    height: "60px",
    maxWidth: "1200px",
    margin: "0 auto",
    width: "100%",
  },
  logoMark: {
    width: "34px",
    height: "34px",
    borderRadius: "9px",
    background: "rgba(255,255,255,0.2)",
    border: "1px solid rgba(255,255,255,0.3)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "17px",
    flexShrink: 0,
  },
  logo: {
    fontSize: "15px",
    fontWeight: "700",
    color: "#ffffff",
    letterSpacing: "-0.01em",
  },
  logoSub: {
    fontSize: "11px",
    color: "rgba(255,255,255,0.65)",
    fontWeight: "400",
    marginTop: "-1px",
  },
  tabBar: {
    display: "flex",
    gap: "2px",
    marginLeft: "8px",
  },
  tab: (active) => ({
    padding: "7px 18px",
    borderRadius: "var(--radius)",
    background: active ? "rgba(255,255,255,0.18)" : "transparent",
    color: active ? "#ffffff" : "rgba(255,255,255,0.65)",
    border: active ? "1px solid rgba(255,255,255,0.25)" : "1px solid transparent",
    cursor: "pointer",
    fontSize: "13px",
    fontWeight: active ? "600" : "400",
    display: "flex",
    alignItems: "center",
    gap: "7px",
    transition: "background 0.15s, color 0.15s",
  }),
  countBadge: {
    background: "#ff5c5c",
    color: "#fff",
    borderRadius: "999px",
    padding: "1px 7px",
    fontSize: "10px",
    fontWeight: "700",
    boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
  },
  authBanner: {
    background: "var(--accent-light)",
    border: "1px solid rgba(74,92,247,0.25)",
    borderRadius: "var(--radius)",
    padding: "11px 18px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "12px",
  },
  content: {
    flex: 1,
    padding: "24px 28px",
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
      <div style={styles.headerWrap}>
        <header style={styles.header}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div style={styles.logoMark}>💼</div>
            <div>
              <div style={styles.logo}>Job Hunt Assistant</div>
              <div style={styles.logoSub}>AI-powered inbox scanner</div>
            </div>
          </div>

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

          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
            {authStatus === true && (
              <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.85)", fontWeight: "500", display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: "#4ade80", display: "inline-block", boxShadow: "0 0 6px #4ade80" }} />
                Connected
              </span>
            )}
            {authStatus === false && (
              <button style={{ fontSize: "12px", padding: "7px 16px", background: "rgba(255,255,255,0.15)", color: "#fff", border: "1px solid rgba(255,255,255,0.3)", borderRadius: "var(--radius)", cursor: "pointer", fontWeight: "500" }} onClick={handleAuthClick}>
                Connect Google
              </button>
            )}
          </div>
        </header>
      </div>

      {authStatus === false && (
        <div style={{ maxWidth: "1200px", margin: "0 auto", width: "100%", padding: "16px 28px 0 28px" }}>
          <div style={styles.authBanner}>
            <span style={{ color: "var(--accent)", fontSize: "13px" }}>
              Connect your Google account to enable inbox scanning and sheet export.
            </span>
            <button className="btn-primary" style={{ flexShrink: 0 }} onClick={handleAuthClick}>
              Connect Google Account
            </button>
          </div>
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
    padding: "11px 14px",
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

  return (
    <div style={{ overflowX: "auto", background: "var(--surface)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-sm)", border: "1px solid var(--border)" }}>
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
