/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

export default function VoiceAssistantButton({ onClick }) {
  return (
    <button
      onClick={onClick}
      title="Ask a question or give a command"
      style={{
        fontSize: "12px",
        padding: "5px 12px",
        background: "rgba(255,255,255,0.1)",
        color: "rgba(255,255,255,0.8)",
        border: "1px solid rgba(255,255,255,0.2)",
        borderRadius: "var(--radius)",
        cursor: "pointer",
      }}
    >
      🎤 Voice
    </button>
  );
}
