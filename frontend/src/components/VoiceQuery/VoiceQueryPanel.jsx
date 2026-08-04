/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import { useEffect, useRef, useState } from "react";
import Modal from "../common/Modal.jsx";

// ai: claude-sonnet-5 | 2026-08-04
// Text input, not a mic button that starts browser speech recognition --
// Chrome's built-in SpeechRecognition streams audio to a Google cloud
// service that turned out to fail with a network error, and is unreliable
// in general. A plain focused text field works with any system dictation
// tool (Vibe Type, Windows/Dragon dictation, etc.) or the keyboard, with no
// dependency on that service at all -- the backend endpoint only ever
// wanted a text string regardless of how it was produced.
export default function VoiceQueryPanel({ transcript, querying, result, onSubmit, onClose }) {
  const [value, setValue] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = () => {
    const text = value.trim();
    if (!text || querying) return;
    onSubmit(text);
    setValue("");
    inputRef.current?.focus();
  };

  return (
    <Modal
      title="Voice Assistant"
      subtitle="Type, or dictate with your system's dictation tool"
      onClose={onClose}
      maxWidth="640px"
    >
      <div style={{ display: "flex", gap: "8px", marginBottom: "16px" }}>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="Ask a question or give a command…"
          disabled={querying}
        />
        <button
          className="btn-primary"
          onClick={submit}
          disabled={querying || !value.trim()}
          style={{ padding: "8px 16px", whiteSpace: "nowrap" }}
        >
          Ask
        </button>
      </div>

      {transcript && (
        <p style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "10px" }}>
          “{transcript}”
        </p>
      )}

      {querying && <p style={{ color: "var(--text-muted)" }}>Thinking…</p>}

      {!querying && result?.error && (
        <p style={{ color: "var(--red)" }}>{result.error}</p>
      )}

      {!querying && result && !result.error && (
        <>
          {result.answer_text && (
            <p style={{ fontSize: "15px", fontWeight: "600", marginBottom: "14px" }}>
              {result.answer_text}
            </p>
          )}

          {result.sql && (
            <div style={{ marginBottom: "14px" }}>
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--text-muted)",
                  marginBottom: "4px",
                  textTransform: "uppercase",
                  letterSpacing: "0.03em",
                }}
              >
                Generated SQL
              </div>
              <pre
                style={{
                  background: "var(--surface2)",
                  padding: "10px 12px",
                  borderRadius: "var(--radius)",
                  fontSize: "12px",
                  overflowX: "auto",
                  margin: 0,
                }}
              >
                {result.sql}
              </pre>
            </div>
          )}

          {Array.isArray(result.rows) && result.rows.length > 0 && (
            <ResultTable columns={result.columns || []} rows={result.rows} />
          )}

          {result.truncated && (
            <p style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "8px" }}>
              Showing the first {result.rows.length} results.
            </p>
          )}

          {result.intent === "action" && (
            <p style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              Action: {result.action} ({result.action_http_status})
            </p>
          )}
        </>
      )}
    </Modal>
  );
}

function ResultTable({ columns, rows }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col}
                style={{
                  textAlign: "left",
                  padding: "6px 8px",
                  borderBottom: "1px solid var(--border)",
                  color: "var(--text-muted)",
                }}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td
                  key={col}
                  style={{ padding: "6px 8px", borderBottom: "1px solid var(--surface2)" }}
                >
                  {String(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
