import React, { useEffect, useRef, useState } from "react";

const SKIP_REASONS = [
  { key: "rejected", label: "Rejected" },
  { key: "duplicate", label: "Duplicate" },
  { key: "req_closed", label: "Req Closed" },
  { key: "not_a_fit", label: "Not a Fit" },
  { key: "not_interested", label: "Not Interested" },
  { key: "other", label: "Other" },
];

const MISMATCH_REASONS = [
  "Not Remote",
  "Hybrid / On-Site",
  "Wrong Level",
  "Wrong Tech Stack",
  "Too Management-Focused",
  "Wrong Domain",
  "Compensation Mismatch",
  "Location Constraint",
  "Contract / Temp",
  "Other Mismatch",
];

export default function JobCard({ job, onAddToSheet, onSkip }) {
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [loading, setLoading] = useState(null); // "sheet" | "skip:<reason>" | "cover" | null
  const [showSkipMenu, setShowSkipMenu] = useState(false);
  // skipConfirm: null | { key, label } — the selected reason awaiting confirmation
  const [skipConfirm, setSkipConfirm] = useState(null);
  const [skipNote, setSkipNote] = useState("");
  const [skipMismatchReasons, setSkipMismatchReasons] = useState([]);
  const [coverLetter, setCoverLetter] = useState(null);
  const [coverLetterOpen, setCoverLetterOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  // Save-to-disk state
  const [saveStatus, setSaveStatus] = useState(null); // null | "saving" | {path, folder} | "error"
  const [folderEditOpen, setFolderEditOpen] = useState(false);
  const [folderInput, setFolderInput] = useState("");

  // Chat state
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState([]); // [{role, content}]
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [pendingUpdate, setPendingUpdate] = useState(null); // updated cover letter from AI
  const chatBottomRef = useRef(null);

  useEffect(() => {
    if (chatOpen && chatBottomRef.current) {
      chatBottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [chatMessages, chatOpen]);

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

  const handleSkipReasonClick = (reason) => {
    setShowSkipMenu(false);
    setSkipConfirm(reason);
    setSkipNote("");
    setSkipMismatchReasons([]);
  };

  const handleSkipConfirm = async () => {
    if (!skipConfirm) return;
    setLoading(`skip:${skipConfirm.key}`);
    const note = skipNote.trim();
    const mismatchReasons = skipMismatchReasons;
    setSkipConfirm(null);
    setSkipNote("");
    setSkipMismatchReasons([]);
    try {
      await onSkip(job.id, skipConfirm.key, note, mismatchReasons);
    } finally {
      setLoading(null);
    }
  };

  const toggleMismatchReason = (reason) => {
    setSkipMismatchReasons((prev) =>
      prev.includes(reason)
        ? prev.filter((item) => item !== reason)
        : [...prev, reason]
    );
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

  const handleSave = async (folderOverride = null) => {
    setSaveStatus("saving");
    setFolderEditOpen(false);
    try {
      const body = {
        text: coverLetter,
        job_title: job.job_title || "",
        company: job.company || "",
      };
      if (folderOverride) body.folder = folderOverride;

      const res = await fetch("/api/cover-letter/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      setSaveStatus({ path: data.path, folder: data.folder });
    } catch (e) {
      setSaveStatus("error");
      alert("Save failed: " + e.message);
    }
  };

  const handleBrowseFolder = async () => {
    try {
      const res = await fetch("/api/system/select-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          initial_path: folderInput || saveStatus?.folder || "",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Folder picker failed");
      if (data.selected_path) {
        setFolderInput(data.selected_path);
      }
    } catch (e) {
      alert("Browse failed: " + e.message);
    }
  };

  const handleChatSend = async () => {
    const text = chatInput.trim();
    if (!text || chatSending) return;

    const newMessages = [...chatMessages, { role: "user", content: text }];
    setChatMessages(newMessages);
    setChatInput("");
    setChatSending(true);

    try {
      const res = await fetch(`/api/jobs/${job.id}/cover-letter/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: newMessages, cover_letter: coverLetter }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Chat failed");

      setChatMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.updated_cover_letter) {
        setPendingUpdate(data.updated_cover_letter);
      }
    } catch (e) {
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${e.message}` },
      ]);
    } finally {
      setChatSending(false);
    }
  };

  const applyPendingUpdate = () => {
    setCoverLetter(pendingUpdate);
    setPendingUpdate(null);
    // Reset save status since the letter changed
    setSaveStatus(null);
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
          <div style={{ fontSize: "15px", fontWeight: "600", color: "var(--text)", lineHeight: "1.4" }}>
            {job.job_title || <em style={{ color: "var(--text-muted)" }}>Unknown Title</em>}
            <span style={{ fontWeight: "400", color: "var(--text-muted)", margin: "0 5px" }}>at</span>
            <span style={{ fontWeight: "600", color: "var(--text)" }}>
              {job.company || <em style={{ fontWeight: "400" }}>Unknown Company</em>}
            </span>
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
          {loading === "sheet" ? "Applying…" : "Applied"}
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
              {SKIP_REASONS.map((reason) => (
                <button
                  key={reason.key}
                  onClick={() => handleSkipReasonClick(reason)}
                  style={skipMenuItemStyle}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  {reason.label}
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

      {/* Skip confirmation panel */}
      {skipConfirm && (
        <div style={{ marginTop: "12px", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "12px 14px" }}>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "8px" }}>
            Skip as <strong style={{ color: "var(--text)" }}>{skipConfirm.label}</strong>
            {skipConfirm.key === "not_a_fit" && (
              <span style={{ opacity: 0.7 }}> — select one or more mismatch reasons to train future scoring</span>
            )}
          </p>
          {skipConfirm.key === "not_a_fit" && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginBottom: "10px" }}>
              {MISMATCH_REASONS.map((reason) => {
                const selected = skipMismatchReasons.includes(reason);
                return (
                  <button
                    key={reason}
                    type="button"
                    onClick={() => toggleMismatchReason(reason)}
                    style={{
                      ...mismatchChipStyle,
                      background: selected ? "var(--accent-light)" : "var(--surface)",
                      borderColor: selected ? "rgba(74,92,247,0.35)" : "var(--border)",
                      color: selected ? "var(--accent)" : "var(--text)",
                    }}
                  >
                    {reason}
                  </button>
                );
              })}
            </div>
          )}
          <input
            type="text"
            value={skipNote}
            onChange={(e) => setSkipNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSkipConfirm(); if (e.key === "Escape") { setSkipConfirm(null); setSkipNote(""); setSkipMismatchReasons([]); } }}
            placeholder={skipConfirm.key === "not_a_fit" ? "Optional note about the mismatch…" : "Optional note…"}
            autoFocus
            style={{
              width: "100%",
              boxSizing: "border-box",
              fontSize: "13px",
              padding: "7px 10px",
              borderRadius: "var(--radius)",
              border: "1px solid var(--border)",
              background: "var(--surface)",
              color: "var(--text)",
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button
              className="btn-danger"
              onClick={handleSkipConfirm}
              disabled={busy}
            >
              {loading?.startsWith("skip:") ? "Skipping…" : `Skip — ${skipConfirm.label}`}
            </button>
            <button
              className="btn-secondary"
              onClick={() => { setSkipConfirm(null); setSkipNote(""); setSkipMismatchReasons([]); }}
              style={{ fontSize: "13px" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Cover letter panel */}
      {coverLetterOpen && coverLetter && (
        <div style={coverLetterPanelStyle}>
          {/* Panel header */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
            <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Cover Letter
            </span>
            <div style={{ display: "flex", gap: "6px" }}>
              <button
                className="btn-secondary"
                onClick={handleCopy}
                style={{ fontSize: "11px", padding: "3px 10px" }}
              >
                {copied ? "Copied!" : "Copy"}
              </button>
              <button
                className="btn-secondary"
                onClick={() => handleSave()}
                disabled={saveStatus === "saving"}
                style={{ fontSize: "11px", padding: "3px 10px" }}
              >
                {saveStatus === "saving" ? "Saving…" : "Save as Word"}
              </button>
              <button
                className="btn-secondary"
                onClick={() => {
                  setChatOpen((o) => !o);
                  setChatMessages([]);
                  setPendingUpdate(null);
                }}
                style={{ fontSize: "11px", padding: "3px 10px" }}
              >
                {chatOpen ? "Close Chat" : "Chat with AI ✦"}
              </button>
            </div>
          </div>

          {/* Pending AI update banner */}
          {pendingUpdate && (
            <div style={{ marginBottom: "10px", padding: "8px 12px", background: "rgba(76,175,129,0.1)", border: "1px solid rgba(76,175,129,0.3)", borderRadius: "var(--radius)", display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={{ fontSize: "12px", color: "var(--green)", flex: 1 }}>
                AI suggested an updated version of your cover letter.
              </span>
              <button className="btn-success" onClick={applyPendingUpdate} style={{ fontSize: "11px", padding: "3px 10px", whiteSpace: "nowrap" }}>
                Use this version
              </button>
              <button className="btn-secondary" onClick={() => setPendingUpdate(null)} style={{ fontSize: "11px", padding: "3px 10px" }}>
                Dismiss
              </button>
            </div>
          )}

          {/* Save confirmation */}
          {saveStatus && saveStatus !== "saving" && saveStatus !== "error" && (
            <div style={saveStatusBoxStyle}>
              <div style={{ fontSize: "12px", color: "var(--green)", fontWeight: "600", marginBottom: "4px" }}>
                Saved as Word
              </div>
              <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "6px", wordBreak: "break-all" }}>
                {saveStatus.path}
              </div>
              <button
                onClick={() => {
                  setFolderEditOpen((o) => !o);
                  setFolderInput(saveStatus.folder);
                }}
                style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: "12px", padding: 0, textDecoration: "underline", fontWeight: "500" }}
              >
                {folderEditOpen ? "Cancel folder edit" : "Change save folder"}
              </button>
            </div>
          )}

          {/* Folder edit input */}
          {folderEditOpen && (
            <div style={{ marginBottom: "10px", display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
              <input
                type="text"
                value={folderInput}
                onChange={(e) => setFolderInput(e.target.value)}
                placeholder="Choose a folder for saved cover letters"
                style={{ flex: 1, minWidth: "280px", fontSize: "12px", padding: "8px 10px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontFamily: "monospace" }}
                onKeyDown={(e) => { if (e.key === "Enter") handleSave(folderInput); }}
              />
              <button className="btn-secondary" onClick={handleBrowseFolder} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>
                Browse…
              </button>
              <button className="btn-secondary" onClick={() => handleSave(folderInput)} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>
                Save here
              </button>
            </div>
          )}

          {/* Cover letter body */}
          <p style={{ fontSize: "13px", lineHeight: "1.7", color: "var(--text)", whiteSpace: "pre-wrap", margin: 0 }}>
            {coverLetter}
          </p>

          {/* Chat panel */}
          {chatOpen && (
            <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
              <p style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
                Chat with AI — ask for edits, adjustments, or feedback
              </p>

              {/* Message history */}
              {chatMessages.length > 0 && (
                <div style={{ maxHeight: "300px", overflowY: "auto", marginBottom: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
                  {chatMessages.map((msg, i) => (
                    <div
                      key={i}
                      style={{
                        alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
                        maxWidth: "85%",
                        background: msg.role === "user" ? "var(--accent)" : "var(--surface)",
                        color: msg.role === "user" ? "#fff" : "var(--text)",
                        border: msg.role === "user" ? "none" : "1px solid var(--border)",
                        borderRadius: "var(--radius)",
                        padding: "8px 12px",
                        fontSize: "13px",
                        lineHeight: "1.5",
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {msg.content}
                    </div>
                  ))}
                  {chatSending && (
                    <div style={{ alignSelf: "flex-start", color: "var(--text-muted)", fontSize: "12px", padding: "4px 8px" }}>
                      Thinking…
                    </div>
                  )}
                  <div ref={chatBottomRef} />
                </div>
              )}

              {/* Input row */}
              <div style={{ display: "flex", gap: "6px" }}>
                <textarea
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleChatSend();
                    }
                  }}
                  placeholder='e.g. "Make it more concise" or "The AI overstated my ML experience"'
                  rows={2}
                  disabled={chatSending}
                  style={{
                    flex: 1,
                    fontSize: "13px",
                    padding: "7px 10px",
                    borderRadius: "var(--radius)",
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    color: "var(--text)",
                    resize: "vertical",
                    fontFamily: "inherit",
                  }}
                />
                <button
                  className="btn-primary"
                  onClick={handleChatSend}
                  disabled={chatSending || !chatInput.trim()}
                  style={{ fontSize: "13px", padding: "7px 14px", alignSelf: "flex-end" }}
                >
                  Send
                </button>
              </div>
              <p style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "5px" }}>
                Enter to send · Shift+Enter for new line
              </p>
            </div>
          )}
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

const mismatchChipStyle = {
  border: "1px solid var(--border)",
  borderRadius: "999px",
  padding: "6px 10px",
  fontSize: "12px",
  cursor: "pointer",
};

const saveStatusBoxStyle = {
  marginBottom: "10px",
  padding: "10px 12px",
  background: "rgba(76,175,129,0.08)",
  border: "1px solid rgba(76,175,129,0.28)",
  borderRadius: "var(--radius)",
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
