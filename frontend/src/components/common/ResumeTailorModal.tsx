/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-07-27
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import Modal from "./Modal.tsx";
import { pickSaveFolder, promptForSaveFolder } from "../../utils/saveFolderUtils.ts";
import { describeFetchError } from "../../utils/fetchErrorUtils.ts";
import type { ChatMessage, ResumeTailorChatResponse, SaveDocumentResponse } from "../../types/coverLetter.ts";
import type { Job } from "../../types/jobs.ts";

const saveStatusBoxStyle: CSSProperties = {
  marginBottom: "10px",
  padding: "10px 12px",
  background: "rgba(76,175,129,0.08)",
  border: "1px solid rgba(76,175,129,0.28)",
  borderRadius: "var(--radius)",
};

interface ResumeTailorSaveStatusDetail {
  path: string;
  folder: string;
}

type ResumeTailorSaveStatus = "saving" | ResumeTailorSaveStatusDetail | null;

interface ResumeTailorModalProps {
  job: Pick<Job, "id" | "job_title" | "company">;
  analysis: string;
  onClose: () => void;
}

/**
 * Self-contained resume tailoring modal. Owns all copy, save-as-markdown, and
 * AI chat state. Props: job (for title/company, save metadata, and chat API),
 * analysis (initial generated analysis text), onClose.
 */
export default function ResumeTailorModal({ job, analysis, onClose }: ResumeTailorModalProps) {
  const [currentAnalysis, setCurrentAnalysis] = useState(analysis);
  const [copied, setCopied] = useState(false);
  const [saveStatus, setSaveStatus] = useState<ResumeTailorSaveStatus>(null);
  const [folderEditOpen, setFolderEditOpen] = useState(false);
  const [folderInput, setFolderInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [pendingUpdate, setPendingUpdate] = useState<string | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [chatMessages]);

  const handleCopy = () => {
    navigator.clipboard.writeText(currentAnalysis);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSave = async (folderOverride: string | null = null) => {
    setSaveStatus("saving");
    setFolderEditOpen(false);
    try {
      const body: Record<string, string> = {
        text: currentAnalysis,
        label: "Resume Tailoring",
        format: "md",
        job_title: job.job_title || "",
        company: job.company || "",
      };
      if (folderOverride) body.folder = folderOverride;
      const res = await fetch("/api/cover-letter/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data: SaveDocumentResponse = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (data.folder_error) {
          const replacement = await promptForSaveFolder(data.folder);
          if (replacement) {
            await handleSave(replacement);
          } else {
            setSaveStatus(null);
          }
          return;
        }
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      setSaveStatus({ path: data.path, folder: data.folder });
    } catch (e) {
      setSaveStatus(null);
      window.alert("Save failed: " + describeFetchError(e));
    }
  };

  const handleBrowseFolder = async () => {
    try {
      const picked = await pickSaveFolder(folderInput || (typeof saveStatus === "object" && saveStatus?.folder) || "");
      if (picked) setFolderInput(picked);
    } catch (e) {
      window.alert("Browse failed: " + describeFetchError(e));
    }
  };

  const handleChatSend = async () => {
    const message = chatInput.trim();
    if (!message || chatSending) return;
    const newMessages: ChatMessage[] = [...chatMessages, { role: "user", content: message }];
    setChatMessages(newMessages);
    setChatInput("");
    setChatSending(true);
    try {
      const res = await fetch(`/api/jobs/${job.id}/resume-tailor/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: newMessages, analysis: currentAnalysis }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Chat failed (${res.status})`);
      }
      const data: ResumeTailorChatResponse = await res.json();
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.updated_analysis) setPendingUpdate(data.updated_analysis);
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: `Error: ${describeFetchError(e)}` }]);
    } finally {
      setChatSending(false);
    }
  };

  const applyPendingUpdate = () => {
    if (pendingUpdate == null) return;
    setCurrentAnalysis(pendingUpdate);
    setPendingUpdate(null);
    setSaveStatus(null);
  };

  const subtitle = `${job.job_title || "Untitled role"}${job.company ? ` at ${job.company}` : ""}`;

  return (
    <Modal title="Resume Tailoring" subtitle={subtitle} onClose={onClose} maxWidth="900px">
      <div style={{ maxHeight: "72vh", overflowY: "auto", paddingRight: "4px" }}>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
          <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Analysis
          </span>
          <div style={{ display: "flex", gap: "6px" }}>
            <button className="btn-secondary" onClick={handleCopy} style={{ fontSize: "11px", padding: "3px 10px" }}>
              {copied ? "Copied!" : "Copy"}
            </button>
            <button
              className="btn-secondary"
              onClick={() => handleSave()}
              disabled={saveStatus === "saving"}
              style={{ fontSize: "11px", padding: "3px 10px" }}
            >
              {saveStatus === "saving" ? "Saving…" : "Save .md"}
            </button>
          </div>
        </div>

        {pendingUpdate && (
          <div style={{ marginBottom: "10px", padding: "8px 12px", background: "rgba(76,175,129,0.1)", border: "1px solid rgba(76,175,129,0.3)", borderRadius: "var(--radius)", display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "12px", color: "var(--green)", flex: 1 }}>
              AI suggested an updated analysis.
            </span>
            <button className="btn-success" onClick={applyPendingUpdate} style={{ fontSize: "11px", padding: "3px 10px", whiteSpace: "nowrap" }}>
              Use updated version
            </button>
            <button className="btn-secondary" onClick={() => setPendingUpdate(null)} style={{ fontSize: "11px", padding: "3px 10px" }}>
              Dismiss
            </button>
          </div>
        )}

        {saveStatus && saveStatus !== "saving" && (
          <div style={saveStatusBoxStyle}>
            <div style={{ fontSize: "12px", color: "var(--green)", fontWeight: "600", marginBottom: "4px" }}>
              Saved as Markdown
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "6px", wordBreak: "break-all" }}>
              {saveStatus.path}
            </div>
            <button
              onClick={() => { setFolderEditOpen((o) => !o); setFolderInput(saveStatus.folder); }}
              style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: "12px", padding: 0, textDecoration: "underline", fontWeight: "500" }}
            >
              {folderEditOpen ? "Cancel folder edit" : "Change save folder"}
            </button>
          </div>
        )}

        {folderEditOpen && (
          <div style={{ marginBottom: "10px", display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="text"
              value={folderInput}
              onChange={(e) => setFolderInput(e.target.value)}
              placeholder="Choose a folder for saved analyses"
              style={{ flex: 1, minWidth: "280px", fontSize: "12px", padding: "8px 10px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontFamily: "monospace" }}
              onKeyDown={(e) => { if (e.key === "Enter") handleSave(folderInput); }}
            />
            <button className="btn-secondary" onClick={handleBrowseFolder} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>Browse…</button>
            <button className="btn-secondary" onClick={() => handleSave(folderInput)} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>Save here</button>
          </div>
        )}

        <p style={{ fontSize: "13px", lineHeight: "1.7", color: "var(--text)", whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>
          {currentAnalysis}
        </p>

        <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
          <p style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
            Chat with AI — ask for corrections, adjustments, or removals
          </p>
          {chatMessages.length > 0 && (
            <div ref={chatMessagesRef} style={{ maxHeight: "300px", overflowY: "auto", marginBottom: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
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
              placeholder='e.g. "The Kubernetes suggestion doesn&apos;t apply — I have no K8s experience" or "The bullet rewrites sound too corporate"'
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

      </div>
    </Modal>
  );
}
