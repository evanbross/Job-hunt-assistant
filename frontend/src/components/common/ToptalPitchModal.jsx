/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-4-6 | 2026-07-03
import { useEffect, useRef, useState } from "react";
import Modal from "./Modal.jsx";

/**
 * Self-contained Toptal Pitch generator. The candidate pastes in a Toptal job
 * description manually (Toptal listings require a login, so there's nothing
 * to fetch), and this generates a short third-person bio for Toptal to pass
 * along to the client, with an AI chat to refine it.
 */
const BACKEND_UNREACHABLE_MESSAGE = "Could not contact the backend — is it running?";

// ai: claude-sonnet-4-6 | 2026-07-06
function describeFetchError(e) {
  if (e instanceof TypeError) return BACKEND_UNREACHABLE_MESSAGE;
  return e.message;
}

export default function ToptalPitchModal({ onClose }) {
  const [jobDescription, setJobDescription] = useState("");
  const [bio, setBio] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [pendingUpdate, setPendingUpdate] = useState(null);
  const chatMessagesRef = useRef(null);

  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [chatMessages]);

  const handleGenerate = async () => {
    const text = jobDescription.trim();
    if (!text || generating) return;
    setGenerating(true);
    setError(null);
    try {
      const res = await fetch("/api/toptal/bio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_description: text }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Generation failed");
      setBio(data.bio);
      setChatMessages([]);
      setPendingUpdate(null);
    } catch (e) {
      setError(describeFetchError(e));
    } finally {
      setGenerating(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(bio);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleChatSend = async () => {
    const text = chatInput.trim();
    if (!text || chatSending) return;
    const newMessages = [...chatMessages, { role: "user", content: text }];
    setChatMessages(newMessages);
    setChatInput("");
    setChatSending(true);
    try {
      const res = await fetch("/api/toptal/bio/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: newMessages,
          job_description: jobDescription,
          current_bio: bio,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Chat failed");
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.updated_bio) setPendingUpdate(data.updated_bio);
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: `Error: ${describeFetchError(e)}` }]);
    } finally {
      setChatSending(false);
    }
  };

  const applyPendingUpdate = () => {
    setBio(pendingUpdate);
    setPendingUpdate(null);
  };

  return (
    <Modal title="Toptal Client Pitch" subtitle="Paste the job description, generate a short third-person bio" onClose={onClose} maxWidth="700px">
      <div style={{ maxHeight: "72vh", overflowY: "auto", paddingRight: "4px" }}>

        <label style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: "6px" }}>
          Client job description
        </label>
        <textarea
          value={jobDescription}
          onChange={(e) => setJobDescription(e.target.value)}
          placeholder="Paste the Toptal job description here…"
          rows={8}
          style={{ width: "100%", fontSize: "13px", padding: "10px 12px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", resize: "vertical", fontFamily: "inherit", boxSizing: "border-box" }}
        />

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "10px" }}>
          <button
            className="btn-primary"
            onClick={handleGenerate}
            disabled={generating || !jobDescription.trim()}
          >
            {generating ? "Generating…" : "Generate Pitch"}
          </button>
        </div>

        {error && (
          <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--red)" }}>
            {error}
          </div>
        )}

        {bio && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "18px", marginBottom: "10px" }}>
              <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Bio
              </span>
              <button className="btn-secondary" onClick={handleCopy} style={{ fontSize: "11px", padding: "3px 10px" }}>
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>

            {pendingUpdate && (
              <div style={{ marginBottom: "10px", padding: "8px 12px", background: "rgba(76,175,129,0.1)", border: "1px solid rgba(76,175,129,0.3)", borderRadius: "var(--radius)", display: "flex", alignItems: "center", gap: "10px" }}>
                <span style={{ fontSize: "12px", color: "var(--green)", flex: 1 }}>
                  AI suggested an updated version of the bio.
                </span>
                <button className="btn-success" onClick={applyPendingUpdate} style={{ fontSize: "11px", padding: "3px 10px", whiteSpace: "nowrap" }}>
                  Use updated version
                </button>
                <button className="btn-secondary" onClick={() => setPendingUpdate(null)} style={{ fontSize: "11px", padding: "3px 10px" }}>
                  Dismiss
                </button>
              </div>
            )}

            <p style={{ fontSize: "13px", lineHeight: "1.7", color: "var(--text)", whiteSpace: "pre-wrap", margin: 0, padding: "10px 12px", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
              {bio}
            </p>

            <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
              <p style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
                Chat with AI — ask for edits, adjustments, or feedback
              </p>
              {chatMessages.length > 0 && (
                <div ref={chatMessagesRef} style={{ maxHeight: "260px", overflowY: "auto", marginBottom: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
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
                </div>
              )}
              <div style={{ display: "flex", gap: "6px" }}>
                <textarea
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChatSend(); } }}
                  placeholder='e.g. "Make it punchier" or "Focus more on the API work"'
                  rows={2}
                  disabled={chatSending}
                  style={{ flex: 1, fontSize: "13px", padding: "7px 10px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", resize: "vertical", fontFamily: "inherit" }}
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
          </>
        )}
      </div>
    </Modal>
  );
}
