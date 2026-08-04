/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

export default function VoiceMicButton({ listening, supported, onToggle }) {
  if (!supported) {
    return (
      <button
        disabled
        title="Voice queries need a browser with Web Speech API support (Chrome or Edge)"
        style={{
          fontSize: "12px",
          padding: "5px 12px",
          background: "rgba(255,255,255,0.05)",
          color: "rgba(255,255,255,0.35)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: "var(--radius)",
          cursor: "not-allowed",
        }}
      >
        🎤 Voice
      </button>
    );
  }

  return (
    <button
      onClick={onToggle}
      title={listening ? "Listening… click to stop" : "Ask a question or give a command"}
      style={{
        fontSize: "12px",
        padding: "5px 12px",
        background: listening ? "#ef4444" : "rgba(255,255,255,0.1)",
        color: listening ? "#fff" : "rgba(255,255,255,0.8)",
        border: listening ? "1px solid #ef4444" : "1px solid rgba(255,255,255,0.2)",
        borderRadius: "var(--radius)",
        cursor: "pointer",
      }}
    >
      {listening ? "🔴 Listening…" : "🎤 Voice"}
    </button>
  );
}
