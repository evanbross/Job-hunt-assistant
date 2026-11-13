/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useState } from "react";
import type { CSSProperties } from "react";
import {
  formatLocalDateTime,
  localInputToUtcIso,
  toLocalDateTimeInputValue,
} from "../utils/dateTimeUtils.ts";
import type { Contact, ContactOutreach, OutreachChannel } from "../types/contacts.ts";
import ContactLeadsSection from "./leads/ContactLeadsSection.tsx";

interface ContactDetailProps {
  contact: Contact;
  onSaveEmailAddress: (contactId: number, address: string) => Promise<boolean>;
  onSaveNotes: (contactId: number, notes: string) => Promise<boolean>;
  // ai: claude-opus-5-5 | 2026-10-14 -- were onLogEmail/onDeleteEmail; logging takes a channel
  onLogOutreach: (contactId: number, sentAtUtc: string, channel: OutreachChannel) => Promise<void>;
  onSetResponse: (contactId: number, outreachId: number, responseReceivedAt: string | null) => Promise<void>;
  onDeleteOutreach: (contactId: number, outreachId: number) => Promise<void>;
  // ai: claude-opus-5-5 | 2026-11-11 -- recruiter lead tracking
  onSavePhone: (contactId: number, phone: string) => Promise<boolean>;
  onSetKeepInformed: (contactId: number, keepInformed: boolean) => Promise<boolean>;
  /** Every contact, for the lead pickers in the leads section. */
  allContacts: Contact[];
}

// ai: claude-opus-5-5 | 2026-10-14
const CHANNEL_OPTIONS: { value: OutreachChannel; label: string }[] = [
  { value: "linkedin", label: "LinkedIn message" },
  { value: "email", label: "Email" },
];

// ai: claude-sonnet-5 | 2026-09-25
/**
 * Expanded row body for one contact: editable email address plus the outreach
 * log (each email or LinkedIn message sent, and the date a reply came back,
 * if any). All writes go through the handlers the panel passes in.
 */
export default function ContactDetail({
  contact,
  onSaveEmailAddress,
  onSaveNotes,
  onLogOutreach,
  onSetResponse,
  onDeleteOutreach,
  onSavePhone,
  onSetKeepInformed,
  allContacts,
}: ContactDetailProps) {
  const [addressDraft, setAddressDraft] = useState(contact.email_address || "");
  const [phoneDraft, setPhoneDraft] = useState(contact.phone || ""); // ai: claude-opus-5-5 | 2026-11-11
  const [notesDraft, setNotesDraft] = useState(contact.notes || "");
  const [sentAtInput, setSentAtInput] = useState(toLocalDateTimeInputValue());
  const [channel, setChannel] = useState<OutreachChannel>("linkedin");
  const [pending, setPending] = useState(false);

  const run = async (action: () => Promise<unknown>) => {
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

  // ai: claude-opus-5-5 | 2026-11-11
  const commitPhone = () => {
    const trimmed = phoneDraft.trim();
    if (trimmed === (contact.phone || "")) return;
    run(async () => {
      const saved = await onSavePhone(contact.id, trimmed);
      if (!saved) setPhoneDraft(contact.phone || "");
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
      window.alert("Pick the date and time you sent the message.");
      return;
    }
    run(() => onLogOutreach(contact.id, sentAtUtc, channel));
  };

  const handleDelete = (entry: ContactOutreach) => {
    if (!window.confirm(`Remove the message logged on ${formatLocalDateTime(entry.sent_at)}?`)) return;
    run(() => onDeleteOutreach(contact.id, entry.id));
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

      {/* ai: claude-opus-5-5 | 2026-11-11 */}
      <div style={{ ...fieldRow, marginTop: "12px" }}>
        <label htmlFor={`contact-phone-${contact.id}`} style={fieldLabel}>
          Phone
        </label>
        <input
          id={`contact-phone-${contact.id}`}
          type="tel"
          value={phoneDraft}
          maxLength={50}
          onChange={(e) => setPhoneDraft(e.target.value)}
          onBlur={commitPhone}
          style={{ flex: 1, maxWidth: "220px" }}
        />
        <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
          <input
            type="checkbox"
            checked={!!contact.keep_informed}
            disabled={pending}
            onChange={(e) => {
              const next = e.target.checked;
              run(() => onSetKeepInformed(contact.id, next));
            }}
          />
          Keep them posted on my search
        </label>
        {contact.keep_informed && (
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            Last reached out: {contact.last_outbound_at ? formatLocalDateTime(contact.last_outbound_at) : "never"}
          </span>
        )}
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

      <div style={{ ...fieldLabel, marginTop: "12px", marginBottom: "6px" }}>Outreach sent</div>
      {contact.outreach.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: "12px", marginBottom: "8px" }}>
          None logged yet.
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: "0 0 8px", padding: 0 }}>
          {contact.outreach.map((entry) => (
            <li key={entry.id} style={outreachRow}>
              <span style={{ minWidth: "150px", fontWeight: 600 }}>{formatLocalDateTime(entry.sent_at)}</span>
              <span className="badge badge-gray">
                {CHANNEL_OPTIONS.find((option) => option.value === entry.channel)?.label ?? entry.channel}
              </span>
              {entry.response_received_at ? (
                <span className="badge badge-green">
                  Replied {formatLocalDateTime(entry.response_received_at)}
                </span>
              ) : (
                <span className="badge badge-orange">No reply yet</span>
              )}
              <span style={{ flex: 1 }} />
              {entry.response_received_at ? (
                <button
                  className="btn-secondary btn-xs"
                  disabled={pending}
                  onClick={() => run(() => onSetResponse(contact.id, entry.id, null))}
                >
                  Clear reply
                </button>
              ) : (
                <button
                  className="btn-success btn-xs"
                  disabled={pending}
                  onClick={() => run(() => onSetResponse(contact.id, entry.id, new Date().toISOString()))}
                >
                  Got a reply now
                </button>
              )}
              <button
                className="btn-danger btn-xs"
                disabled={pending}
                onClick={() => handleDelete(entry)}
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
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value as OutreachChannel)}
          aria-label="How the message was sent"
          style={{ width: "auto" }}
        >
          {CHANNEL_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button className="btn-primary btn-sm" disabled={pending} onClick={handleLog}>
          Log message sent
        </button>
      </div>

      <ContactLeadsSection contact={contact} contacts={allContacts} />
    </div>
  );
}

const detailBox: CSSProperties = {
  padding: "12px 14px 14px 44px",
  background: "var(--surface2)",
  borderTop: "1px solid var(--border)",
};

const fieldRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
};

const fieldLabel: CSSProperties = {
  fontSize: "12px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  minWidth: "90px",
};

const outreachRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "5px 0",
  fontSize: "13px",
};
