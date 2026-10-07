/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-08-19
import type { CSSProperties } from "react";
import Modal from "./Modal.tsx";

const inputStyle: CSSProperties = {
  width: "80px",
  fontSize: "14px",
  padding: "8px 10px",
  borderRadius: "var(--radius)",
  border: "1px solid var(--border)",
  background: "var(--surface)",
  color: "var(--text)",
  fontFamily: "inherit",
};

interface SnoozeModalProps {
  /** Current draft value. */
  days: string;
  onDaysChange: (value: string) => void;
  onConfirm: () => void;
  onClose: () => void;
  busy: boolean;
}

/**
 * Small modal dialog for choosing how many days to snooze a job.
 */
export default function SnoozeModal({ days, onDaysChange, onConfirm, onClose, busy }: SnoozeModalProps) {
  return (
    <Modal
      title="Snooze this job"
      onClose={onClose}
      maxWidth="360px"
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" onClick={onConfirm} disabled={busy}>
            {busy ? "Snoozing…" : "Snooze"}
          </button>
        </>
      }
    >
      <p style={{ fontSize: "13px", color: "var(--text-muted)", marginBottom: "12px" }}>
        Hide this job for how many days?
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <input
          type="number"
          min="1"
          max="365"
          value={days}
          onChange={(e) => onDaysChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onConfirm();
          }}
          autoFocus
          style={inputStyle}
        />
        <span style={{ fontSize: "13px", color: "var(--text-muted)" }}>days</span>
      </div>
    </Modal>
  );
}
