import React, { useEffect, useState } from "react";
import ResumeUpload from "./ResumeUpload.jsx";

export default function SettingsPanel() {
  const [activeSection, setActiveSection] = useState("resume");

  const sections = [
    { id: "resume", label: "Resume" },
    { id: "sources", label: "Email Sources" },
    { id: "penalties", label: "Scoring Rules" },
    { id: "feedback", label: "Learned Patterns" },
    { id: "config", label: "Configuration" },
  ];

  return (
    <div style={{ display: "flex", gap: "20px" }}>
      {/* Sidebar nav */}
      <nav style={sideNav}>
        {sections.map((s) => (
          <button
            key={s.id}
            onClick={() => setActiveSection(s.id)}
            style={navBtn(activeSection === s.id)}
          >
            {s.label}
          </button>
        ))}
      </nav>

      {/* Content */}
      <div style={{ flex: 1 }}>
        {activeSection === "resume" && <ResumeUpload />}
        {activeSection === "sources" && <EmailSourcesSection />}
        {activeSection === "penalties" && <PenaltiesSection />}
        {activeSection === "feedback" && <FeedbackSection />}
        {activeSection === "config" && <ConfigSection />}
      </div>
    </div>
  );
}

// ── Email Sources ─────────────────────────────────────────────────────────────

function EmailSourcesSection() {
  const [sources, setSources] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", match_type: "sender_domain", match_value: "" });
  const [editId, setEditId] = useState(null);

  useEffect(() => { fetchSources(); }, []);

  const fetchSources = async () => {
    const res = await fetch("/api/sources");
    const data = await res.json();
    setSources(Array.isArray(data) ? data : []);
  };

  const toggleEnabled = async (source) => {
    await fetch(`/api/sources/${source.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: source.enabled ? 0 : 1 }),
    });
    fetchSources();
  };

  const deleteSource = async (id) => {
    if (!confirm("Delete this source?")) return;
    await fetch(`/api/sources/${id}`, { method: "DELETE" });
    fetchSources();
  };

  const startEdit = (source) => {
    setEditId(source.id);
    setForm({ name: source.name, match_type: source.match_type, match_value: source.match_value });
    setShowForm(true);
  };

  const submitForm = async () => {
    if (!form.name || !form.match_value) return;
    if (editId) {
      await fetch(`/api/sources/${editId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
    } else {
      await fetch("/api/sources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
    }
    setShowForm(false);
    setEditId(null);
    setForm({ name: "", match_type: "sender_domain", match_value: "" });
    fetchSources();
  };

  return (
    <div>
      <SectionHeader title="Email Sources" action={{ label: "Add Source", onClick: () => { setEditId(null); setForm({ name: "", match_type: "sender_domain", match_value: "" }); setShowForm(true); } }} />

      {showForm && (
        <div style={formBox}>
          <div style={formGrid}>
            <label style={labelStyle}>Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="LinkedIn Jobs" />
            </label>
            <label style={labelStyle}>Match Type
              <select value={form.match_type} onChange={(e) => setForm({ ...form, match_type: e.target.value })}>
                <option value="sender_domain">Sender Domain</option>
                <option value="sender_email">Sender Email</option>
                <option value="subject_contains">Subject Contains</option>
              </select>
            </label>
            <label style={labelStyle}>Match Value
              <input value={form.match_value} onChange={(e) => setForm({ ...form, match_value: e.target.value })} placeholder="linkedin.com" />
            </label>
          </div>
          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
            <button className="btn-primary" onClick={submitForm}>{editId ? "Save" : "Add"}</button>
            <button className="btn-secondary" onClick={() => { setShowForm(false); setEditId(null); }}>Cancel</button>
          </div>
        </div>
      )}

      <table style={tableStyle}>
        <thead>
          <tr>{["Name", "Type", "Value", "Enabled", "Actions"].map((h) => <th key={h} style={thStyle}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.id}>
              <td style={tdStyle}>{s.name}</td>
              <td style={tdStyle}><span className="badge badge-gray">{s.match_type}</span></td>
              <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: "12px" }}>{s.match_value}</td>
              <td style={tdStyle}>
                <Toggle checked={!!s.enabled} onChange={() => toggleEnabled(s)} />
              </td>
              <td style={tdStyle}>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button className="btn-secondary" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={() => startEdit(s)}>Edit</button>
                  <button className="btn-danger" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={() => deleteSource(s.id)}>Delete</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Penalty Rules ─────────────────────────────────────────────────────────────

function PenaltiesSection() {
  const [rules, setRules] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ rule_text: "", penalty_points: "" });
  const [editId, setEditId] = useState(null);

  useEffect(() => { fetchRules(); }, []);

  const fetchRules = async () => {
    const res = await fetch("/api/penalties");
    const data = await res.json();
    setRules(Array.isArray(data) ? data : []);
  };

  const toggleEnabled = async (rule) => {
    await fetch(`/api/penalties/${rule.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: rule.enabled ? 0 : 1 }),
    });
    fetchRules();
  };

  const deleteRule = async (id) => {
    if (!confirm("Delete this rule?")) return;
    await fetch(`/api/penalties/${id}`, { method: "DELETE" });
    fetchRules();
  };

  const startEdit = (rule) => {
    setEditId(rule.id);
    setForm({ rule_text: rule.rule_text, penalty_points: String(rule.penalty_points) });
    setShowForm(true);
  };

  const submitForm = async () => {
    if (!form.rule_text || !form.penalty_points) return;
    const body = { rule_text: form.rule_text, penalty_points: parseInt(form.penalty_points, 10) };
    if (editId) {
      await fetch(`/api/penalties/${editId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } else {
      await fetch("/api/penalties", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    setShowForm(false);
    setEditId(null);
    setForm({ rule_text: "", penalty_points: "" });
    fetchRules();
  };

  return (
    <div>
      <SectionHeader title="Scoring Penalties" action={{ label: "Add Rule", onClick: () => { setEditId(null); setForm({ rule_text: "", penalty_points: "" }); setShowForm(true); } }} />
      <p style={{ color: "var(--text-muted)", fontSize: "12px", marginBottom: "14px" }}>
        If a rule's text appears in a job email, that many points are deducted from the fit score.
      </p>

      {showForm && (
        <div style={formBox}>
          <div style={formGrid}>
            <label style={labelStyle}>Rule Text (phrase to match)
              <input value={form.rule_text} onChange={(e) => setForm({ ...form, rule_text: e.target.value })} placeholder="on-site" />
            </label>
            <label style={labelStyle}>Points to Deduct
              <input type="number" min="1" max="100" value={form.penalty_points} onChange={(e) => setForm({ ...form, penalty_points: e.target.value })} placeholder="40" />
            </label>
          </div>
          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
            <button className="btn-primary" onClick={submitForm}>{editId ? "Save" : "Add"}</button>
            <button className="btn-secondary" onClick={() => { setShowForm(false); setEditId(null); }}>Cancel</button>
          </div>
        </div>
      )}

      <table style={tableStyle}>
        <thead>
          <tr>{["Phrase", "Penalty", "Enabled", "Actions"].map((h) => <th key={h} style={thStyle}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}>
              <td style={tdStyle}><code style={{ background: "var(--surface2)", padding: "2px 6px", borderRadius: "4px", fontSize: "12px" }}>{r.rule_text}</code></td>
              <td style={tdStyle}><span className="badge badge-red">-{r.penalty_points} pts</span></td>
              <td style={tdStyle}><Toggle checked={!!r.enabled} onChange={() => toggleEnabled(r)} /></td>
              <td style={tdStyle}>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button className="btn-secondary" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={() => startEdit(r)}>Edit</button>
                  <button className="btn-danger" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={() => deleteRule(r.id)}>Delete</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Learned Patterns ──────────────────────────────────────────────────────────

function FeedbackSection() {
  const [feedback, setFeedback] = useState([]);

  useEffect(() => {
    fetch("/api/feedback")
      .then((r) => r.json())
      .then((d) => setFeedback(Array.isArray(d) ? d : []));
  }, []);

  const deleteFeedback = async (id) => {
    await fetch(`/api/feedback/${id}`, { method: "DELETE" });
    setFeedback((prev) => prev.filter((f) => f.id !== id));
  };

  return (
    <div>
      <SectionHeader title="Learned Patterns" />
      <p style={{ color: "var(--text-muted)", fontSize: "12px", marginBottom: "14px" }}>
        These patterns were learned when you approved or rejected triage emails. Delete a pattern
        to forget it.
      </p>

      {feedback.length === 0 ? (
        <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>No patterns learned yet.</p>
      ) : (
        <table style={tableStyle}>
          <thead>
            <tr>{["Sender", "Subject Pattern", "Label", "Date", ""].map((h, i) => <th key={i} style={thStyle}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {feedback.map((f) => (
              <tr key={f.id}>
                <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: "12px" }}>{f.sender_email}</td>
                <td style={tdStyle}>{f.subject_pattern || "—"}</td>
                <td style={tdStyle}>
                  <span className={`badge ${f.is_relevant ? "badge-green" : "badge-red"}`}>
                    {f.is_relevant ? "Job Posting" : "Noise"}
                  </span>
                </td>
                <td style={tdStyle}>{f.created_at?.slice(0, 10) || "—"}</td>
                <td style={tdStyle}>
                  <button className="btn-danger" style={{ padding: "3px 10px", fontSize: "12px" }} onClick={() => deleteFeedback(f.id)}>
                    Forget
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Configuration ─────────────────────────────────────────────────────────────

function ConfigSection() {
  const [cfg, setCfg] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((d) => setCfg(d));
  }, []);

  const save = async () => {
    const res = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    if (res.ok) {
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  };

  if (!cfg) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  return (
    <div>
      <SectionHeader title="Configuration" />

      <div style={{ maxWidth: "360px" }}>
        <label style={labelStyle}>
          Email Lookback (days)
          <input
            type="number"
            min="1"
            max="90"
            value={cfg.email_lookback_days ?? 10}
            onChange={(e) => setCfg({ ...cfg, email_lookback_days: parseInt(e.target.value, 10) })}
          />
          <span style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px", display: "block" }}>
            Scan emails from the past N days (1–90).
          </span>
        </label>

        <div style={{ marginTop: "14px", display: "flex", alignItems: "center", gap: "10px" }}>
          <button className="btn-primary" onClick={save}>Save</button>
          {saved && <span style={{ color: "var(--green)", fontSize: "13px" }}>Saved!</span>}
        </div>
      </div>
    </div>
  );
}

// ── Shared UI atoms ───────────────────────────────────────────────────────────

function SectionHeader({ title, action }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
      <h2 style={{ fontSize: "16px", fontWeight: "700" }}>{title}</h2>
      {action && (
        <button className="btn-primary" style={{ padding: "6px 14px" }} onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <button
      onClick={onChange}
      style={{
        width: "38px",
        height: "20px",
        borderRadius: "10px",
        background: checked ? "var(--green)" : "var(--border)",
        border: "none",
        padding: 0,
        cursor: "pointer",
        position: "relative",
        transition: "background 0.2s",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: "2px",
          left: checked ? "18px" : "2px",
          width: "16px",
          height: "16px",
          borderRadius: "50%",
          background: "#fff",
          transition: "left 0.2s",
        }}
      />
    </button>
  );
}

// ── Shared styles ─────────────────────────────────────────────────────────────

const sideNav = {
  width: "160px",
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  gap: "2px",
};

const navBtn = (active) => ({
  textAlign: "left",
  padding: "8px 12px",
  borderRadius: "var(--radius)",
  background: active ? "var(--surface2)" : "transparent",
  color: active ? "var(--text)" : "var(--text-muted)",
  border: active ? "1px solid var(--border)" : "1px solid transparent",
  cursor: "pointer",
  fontSize: "13px",
  fontWeight: active ? "600" : "400",
});

const labelStyle = {
  display: "flex",
  flexDirection: "column",
  gap: "4px",
  color: "var(--text-muted)",
  fontSize: "12px",
  fontWeight: "600",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

const formBox = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "14px",
  marginBottom: "16px",
};

const formGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
  gap: "12px",
};

const tableStyle = {
  width: "100%",
  borderCollapse: "collapse",
  background: "var(--surface)",
  borderRadius: "var(--radius)",
  overflow: "hidden",
  border: "1px solid var(--border)",
};

const thStyle = {
  padding: "9px 12px",
  textAlign: "left",
  background: "var(--surface2)",
  color: "var(--text-muted)",
  fontSize: "11px",
  fontWeight: "600",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderBottom: "1px solid var(--border)",
};

const tdStyle = {
  padding: "10px 12px",
  borderBottom: "1px solid var(--border)",
  color: "var(--text)",
  verticalAlign: "middle",
};
