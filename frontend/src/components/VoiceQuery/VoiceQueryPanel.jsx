/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

import Modal from "../common/Modal.jsx";

export default function VoiceQueryPanel({ transcript, querying, result, onClose }) {
  return (
    <Modal
      title="Voice Assistant"
      subtitle={transcript ? `“${transcript}”` : undefined}
      onClose={onClose}
      maxWidth="640px"
    >
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
