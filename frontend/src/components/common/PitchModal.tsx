/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-07-27
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import Modal from "./Modal.tsx";
import PhraseBookModal from "./PhraseBookModal.tsx"; // ai: claude-sonnet-5 | 2026-08-11
import { pickSaveFolder, promptForSaveFolder } from "../../utils/saveFolderUtils.ts";
import { describeFetchError } from "../../utils/fetchErrorUtils.ts";
import type { ChatMessage, PitchChatResponse, PitchType, SaveDocumentResponse, SaveStatus } from "../../types/coverLetter.ts";
import type { Job } from "../../types/jobs.ts";

const saveMenuStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  right: 0,
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
  zIndex: 200,
  minWidth: "130px",
  padding: "4px",
  display: "flex",
  flexDirection: "column",
};

const saveMenuItemStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: "7px 10px",
  fontSize: "12px",
  textAlign: "left",
  cursor: "pointer",
  color: "var(--text)",
  borderRadius: "4px",
  width: "100%",
};

const saveStatusBoxStyle: CSSProperties = {
  marginBottom: "10px",
  padding: "10px 12px",
  background: "rgba(76,175,129,0.08)",
  border: "1px solid rgba(76,175,129,0.28)",
  borderRadius: "var(--radius)",
};

interface PitchModalProps {
  job: Pick<Job, "id" | "job_title" | "company">;
  pitchType: PitchType;
  text: string;
  onClose: () => void;
}

/**
 * Self-contained pitch modal. Owns all copy, save-as-file, and AI chat state.
 * Props: job (for title/company and chat API), pitchType ("why_company" |
 * "why_fit"), text (initial generated pitch), onClose.
 */
export default function PitchModal({ job, pitchType, text, onClose }: PitchModalProps) {
  const [currentText, setCurrentText] = useState(text);
  const [copied, setCopied] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);
  const [showSaveMenu, setShowSaveMenu] = useState(false);
  const [folderEditOpen, setFolderEditOpen] = useState(false);
  const [folderInput, setFolderInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [pendingUpdate, setPendingUpdate] = useState<string | null>(null);
  const [phraseBookMode, setPhraseBookMode] = useState<"insert" | "save" | null>(null);
  const [phraseBookSaveText, setPhraseBookSaveText] = useState("");
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  const title = pitchType === "why_company" ? "Why I want to work here" : "Why I'd be good for this job";

  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [chatMessages]);

  const handleCopy = () => {
    navigator.clipboard.writeText(currentText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSave = async (format = "docx", folderOverride: string | null = null) => {
    setSaveStatus("saving");
    setShowSaveMenu(false);
    setFolderEditOpen(false);
    try {
      const label = pitchType === "why_company" ? "Why Company" : "Why Fit";
      const body: Record<string, string> = {
        text: currentText,
        label,
        format,
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
            await handleSave(format, replacement);
          } else {
            setSaveStatus(null);
          }
          return;
        }
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      setSaveStatus({ path: data.path, folder: data.folder, format });
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
      const res = await fetch(`/api/jobs/${job.id}/pitch/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: newMessages, current_text: currentText, type: pitchType }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Chat failed (${res.status})`);
      }
      const data: PitchChatResponse = await res.json();
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.updated_pitch) setPendingUpdate(data.updated_pitch);
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: `Error: ${describeFetchError(e)}` }]);
    } finally {
      setChatSending(false);
    }
  };

  const applyPendingUpdate = () => {
    if (pendingUpdate == null) return;
    setCurrentText(pendingUpdate);
    setPendingUpdate(null);
    setSaveStatus(null);
  };

  // ai: claude-sonnet-5 | 2026-08-11
  const handlePhraseInsert = (phraseText: string) => {
    setChatInput(
      `Please work this in, adapting it to fit naturally:\n\n${phraseText}`
    );
  };

  const subtitle = `${job.job_title || "Untitled role"}${job.company ? ` at ${job.company}` : ""}`;

  return (
    <Modal title={title} subtitle={subtitle} onClose={onClose} maxWidth="900px">
      <div style={{ maxHeight: "72vh", overflowY: "auto", paddingRight: "4px" }}>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
          <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Draft
          </span>
          <div style={{ display: "flex", gap: "6px" }}>
            {pitchType === "why_company" && (
              <button className="btn-secondary" onClick={() => setPhraseBookMode("insert")} style={{ fontSize: "11px", padding: "3px 10px" }}>
                Phrase Book
              </button>
            )}
            <button className="btn-secondary" onClick={handleCopy} style={{ fontSize: "11px", padding: "3px 10px" }}>
              {copied ? "Copied!" : "Copy"}
            </button>
            <div style={{ position: "relative" }}>
              <button
                className="btn-secondary"
                onClick={() => setShowSaveMenu((o) => !o)}
                disabled={saveStatus === "saving"}
                style={{ fontSize: "11px", padding: "3px 10px", display: "flex", alignItems: "center", gap: "3px" }}
              >
                {saveStatus === "saving" ? "Saving…" : "Save As"}
                <span style={{ fontSize: "8px", opacity: 0.7 }}>▾</span>
              </button>
              {showSaveMenu && (
                <div style={saveMenuStyle}>
                  <button onClick={() => handleSave("text")} style={saveMenuItemStyle}>Text (.txt)</button>
                  <button onClick={() => handleSave("docx")} style={saveMenuItemStyle}>Word (.docx)</button>
                  <button onClick={() => handleSave("pdf")}  style={saveMenuItemStyle}>PDF (.pdf)</button>
                </div>
              )}
            </div>
          </div>
        </div>

        {pendingUpdate && (
          <div style={{ marginBottom: "10px", padding: "8px 12px", background: "rgba(76,175,129,0.1)", border: "1px solid rgba(76,175,129,0.3)", borderRadius: "var(--radius)", display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "12px", color: "var(--green)", flex: 1 }}>
              AI suggested an updated version.
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
              Saved as {saveStatus.format === "pdf" ? "PDF" : saveStatus.format === "text" ? "Text" : "Word"}
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
              placeholder="Choose a folder for saved pitches"
              style={{ flex: 1, minWidth: "280px", fontSize: "12px", padding: "8px 10px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontFamily: "monospace" }}
              onKeyDown={(e) => { if (e.key === "Enter") handleSave(typeof saveStatus === "object" && saveStatus?.format || "docx", folderInput); }}
            />
            <button className="btn-secondary" onClick={handleBrowseFolder} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>Browse…</button>
            <button className="btn-secondary" onClick={() => handleSave(typeof saveStatus === "object" && saveStatus?.format || "docx", folderInput)} style={{ fontSize: "12px", padding: "8px 12px", whiteSpace: "nowrap" }}>Save here</button>
          </div>
        )}

        <p style={{ fontSize: "13px", lineHeight: "1.7", color: "var(--text)", whiteSpace: "pre-wrap", margin: 0 }}>
          {currentText}
        </p>
        {pitchType === "why_company" && (
          <button
            onClick={() => { setPhraseBookSaveText(currentText); setPhraseBookMode("save"); }}
            style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: "12px", padding: "6px 0 0 0", textDecoration: "underline" }}
          >
            Save this to Phrase Book
          </button>
        )}

        <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
          <p style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
            Chat with AI — ask for edits, adjustments, or feedback
          </p>
          {chatMessages.length > 0 && (
            <div ref={chatMessagesRef} style={{ maxHeight: "300px", overflowY: "auto", marginBottom: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
              {chatMessages.map((msg, i) => (
                <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: msg.role === "user" ? "flex-end" : "flex-start", maxWidth: "85%", alignSelf: msg.role === "user" ? "flex-end" : "flex-start" }}>
                  <div
                    style={{
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
                  {msg.role === "user" && pitchType === "why_company" && (
                    <button
                      onClick={() => { setPhraseBookSaveText(msg.content); setPhraseBookMode("save"); }}
                      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "10px", padding: "2px 2px 0 2px", textDecoration: "underline" }}
                    >
                      Save this to Phrase Book
                    </button>
                  )}
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
              placeholder='e.g. "Make it more specific to their product" or "Too formal"'
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

      {phraseBookMode === "insert" && (
        <PhraseBookModal onClose={() => setPhraseBookMode(null)} onInsert={handlePhraseInsert} />
      )}
      {phraseBookMode === "save" && (
        <PhraseBookModal onClose={() => setPhraseBookMode(null)} initialText={phraseBookSaveText} />
      )}
    </Modal>
  );
}
