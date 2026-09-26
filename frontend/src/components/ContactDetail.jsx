/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 * You may read, study, and run this code for personal purposes.
 * You may NOT:
 *   - Use this code, in whole or in part, in any commercial product or service
 *   - Sell, sublicense, or otherwise monetize this code or derivatives of it
 *   - Redistribute this code without explicit written permission from ZombieTech LLC
 *
 * No warranty is provided, express or implied. Use at your own risk.
 *
 * Licensing inquiries: license@zombietech.biz
 */

import { useState } from "react";
import {
  formatLocalDateTime,
  localInputToUtcIso,
  toLocalDateTimeInputValue,
} from "../utils/dateTimeUtils.js";

// ai: claude-sonnet-5 | 2026-09-25
/**
 * Expanded row body for one contact: editable email address plus the outreach
 * log (each email sent, and the date a reply came back, if any). All writes
 * go through the handlers the panel passes in.
 */
export default function ContactDetail({
  contact,
  onSaveEmailAddress,
  onSaveNotes,
  onLogEmail,
  onSetResponse,
  onDeleteEmail,
}) {
  const [addressDraft, setAddressDraft] = useState(contact.email_address || "");
  const [notesDraft, setNotesDraft] = useState(contact.notes || "");
  const [sentAtInput, setSentAtInput] = useState(toLocalDateTimeInputValue());
  const [pending, setPending] = useState(false);

  const run = async (action) => {
    setPending(true);
    try {
      await action();
    } finally {
      setPending(false);
    }
  };

  const commitAddress = () => {
    const trimmed = addressDraft.trim();
    if (trimmed === (contact.email_address || "")) return;
    run(async () => {
      const saved = await onSaveEmailAddress(contact.id, trimmed);
      if (!saved) setAddressDraft(contact.email_address || "");
    });
  };

  const commitNotes = () => {
    const trimmed = notesDraft.trim();
    if (trimmed === (contact.notes || "")) return;
    run(async () => {
      const saved = await onSaveNotes(contact.id, trimmed);
      if (!saved) setNotesDraft(contact.notes || "");
    });
  };

  const handleLog = () => {
    const sentAtUtc = localInputToUtcIso(sentAtInput);
    if (!sentAtUtc) {
      window.alert("Pick the date and time you sent the email.");
      return;
    }
    run(() => onLogEmail(contact.id, sentAtUtc));
  };

  const handleDelete = (email) => {
    if (!window.confirm(`Remove the email logged on ${formatLocalDateTime(email.sent_at)}?`)) return;
    run(() => onDeleteEmail(contact.id, email.id));
  };

  return (
    <div style={detailBox}>
      <div style={fieldRow}>
        <label htmlFor={`contact-email-${contact.id}`} style={fieldLabel}>
          Email address
        </label>
        <input
          id={`contact-email-${contact.id}`}
          type="email"
          value={addressDraft}
          placeholder="Not provided (LinkedIn hides most)"
          onChange={(e) => setAddressDraft(e.target.value)}
          onBlur={commitAddress}
          style={{ flex: 1, maxWidth: "320px" }}
        />
      </div>

      <div style={{ ...fieldRow, alignItems: "flex-start", marginTop: "12px" }}>
        <label
          htmlFor={`contact-notes-${contact.id}`}
          style={{ ...fieldLabel, paddingTop: "6px" }}
        >
          Notes
        </label>
        <textarea
          id={`contact-notes-${contact.id}`}
          value={notesDraft}
          rows={3}
          maxLength={5000}
          placeholder="Where you met, what to ask, follow-up ideas"
          onChange={(e) => setNotesDraft(e.target.value)}
          onBlur={commitNotes}
          style={{ flex: 1, maxWidth: "480px", resize: "vertical" }}
        />
      </div>

      <div style={{ ...fieldLabel, marginTop: "12px", marginBottom: "6px" }}>Emails sent</div>
      {contact.emails.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: "12px", marginBottom: "8px" }}>
          None logged yet.
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: "0 0 8px", padding: 0 }}>
          {contact.emails.map((email) => (
            <li key={email.id} style={emailRow}>
              <span style={{ minWidth: "150px", fontWeight: 600 }}>{formatLocalDateTime(email.sent_at)}</span>
              {email.response_received_at ? (
                <span className="badge badge-green">
                  Replied {formatLocalDateTime(email.response_received_at)}
                </span>
              ) : (
                <span className="badge badge-orange">No reply yet</span>
              )}
              <span style={{ flex: 1 }} />
              {email.response_received_at ? (
                <button
                  className="btn-secondary btn-xs"
                  disabled={pending}
                  onClick={() => run(() => onSetResponse(contact.id, email.id, null))}
                >
                  Clear reply
                </button>
              ) : (
                <button
                  className="btn-success btn-xs"
                  disabled={pending}
                  onClick={() => run(() => onSetResponse(contact.id, email.id, new Date().toISOString()))}
                >
                  Got a reply now
                </button>
              )}
              <button
                className="btn-danger btn-xs"
                disabled={pending}
                onClick={() => handleDelete(email)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <div style={fieldRow}>
        <label htmlFor={`contact-sent-${contact.id}`} style={fieldLabel}>
          Sent on
        </label>
        <input
          id={`contact-sent-${contact.id}`}
          type="datetime-local"
          value={sentAtInput}
          onChange={(e) => setSentAtInput(e.target.value)}
        />
        <button className="btn-primary btn-sm" disabled={pending} onClick={handleLog}>
          Log email sent
        </button>
      </div>
    </div>
  );
}

const detailBox = {
  padding: "12px 14px 14px 44px",
  background: "var(--surface2)",
  borderTop: "1px solid var(--border)",
};

const fieldRow = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
};

const fieldLabel = {
  fontSize: "12px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  minWidth: "90px",
};

const emailRow = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "5px 0",
  fontSize: "13px",
};
