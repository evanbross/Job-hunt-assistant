/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import Modal from "./common/Modal.tsx";
import type { ApiErrorBody } from "../types/common.ts";
import type { JobStatusHistoryEntry } from "../types/jobs.ts";

const STATUS_LABELS: Record<string, string> = {
  pending_review: "Queue",
  triage: "Triage",
  applied: "Applied",
  skipped: "Skipped",
  possible_duplicate: "Duplicate",
};

const TRIGGER_LABELS: Record<string, string> = {
  apply: "Marked applied",
  skip: "Skipped",
  approve: "Approved from triage",
  return_to_queue: "Returned to queue",
  patch: "Status edited",
};

function statusLabel(s: string): string {
  return STATUS_LABELS[s] || s;
}

function triggerLabel(t: string): string {
  return TRIGGER_LABELS[t] || t;
}

function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface RecentChangesModalProps {
  onClose: () => void;
  trigger?: string | null;
  title?: string;
  subtitle?: string;
}

export default function RecentChangesModal({
  onClose,
  trigger = null,
  title = "Recent Status Changes",
  subtitle = "Undo an accidental status change. Only the most recent 30 changes are shown.",
}: RecentChangesModalProps) {
  const [entries, setEntries] = useState<JobStatusHistoryEntry[] | null>(null);
  const [undoing, setUndoing] = useState<number | null>(null);

  useEffect(() => {
    const query = trigger ? `limit=30&trigger=${encodeURIComponent(trigger)}` : "limit=30";
    fetch(`/api/jobs/status-history?${query}`)
      .then((r) => r.json())
      .then((data: JobStatusHistoryEntry[]) => setEntries(Array.isArray(data) ? data : []))
      .catch(() => setEntries([]));
  }, [trigger]);

  const undo = async (entry: JobStatusHistoryEntry) => {
    setUndoing(entry.id);
    const res = await fetch(`/api/jobs/status-history/${entry.id}/undo`, { method: "POST" });
    setUndoing(null);
    if (!res.ok) {
      const data: ApiErrorBody = await res.json().catch(() => ({}));
      window.alert(data.error || `Undo failed (${res.status})`);
      return;
    }
    setEntries((prev) => (prev ? prev.filter((e) => e.id !== entry.id) : prev));
  };

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      maxWidth="560px"
    >
      {entries === null ? (
        <p style={{ color: "var(--text-muted)", fontSize: "13px", padding: "8px 0" }}>Loading…</p>
      ) : entries.length === 0 ? (
        <p style={{ color: "var(--text-muted)", fontSize: "13px", padding: "8px 0" }}>No recent changes recorded.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
          {entries.map((entry) => (
            <div
              key={entry.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "12px",
                padding: "10px 12px",
                background: "var(--surface2)",
                borderRadius: "var(--radius)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {entry.job_title || "Untitled"}{entry.company ? ` — ${entry.company}` : ""}
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "2px", display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
                  <span>{triggerLabel(entry.trigger)}</span>
                  <span style={{ opacity: 0.5 }}>·</span>
                  <span>
                    <span style={chipStyle("gray")}>{statusLabel(entry.from_status)}</span>
                    {" → "}
                    <span style={chipStyle("accent")}>{statusLabel(entry.to_status)}</span>
                  </span>
                  <span style={{ opacity: 0.5 }}>·</span>
                  <span>{timeAgo(entry.changed_at)}</span>
                </div>
              </div>
              <button
                className="btn-secondary"
                style={{ fontSize: "12px", padding: "4px 12px", flexShrink: 0 }}
                disabled={undoing === entry.id}
                onClick={() => undo(entry)}
              >
                {undoing === entry.id ? "…" : "Undo"}
              </button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function chipStyle(variant: "accent" | "gray"): CSSProperties {
  const base: CSSProperties = {
    display: "inline-block",
    padding: "1px 6px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: 600,
  };
  if (variant === "accent") {
    return { ...base, background: "var(--accent-light)", color: "var(--accent)" };
  }
  return { ...base, background: "var(--surface)", color: "var(--text-muted)", border: "1px solid var(--border)" };
}
