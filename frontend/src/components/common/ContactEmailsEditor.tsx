/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-11-26
// A contact's typed email addresses: list, add, edit in place, remove. Shared by
// ContactDetail.tsx (the Contacts tab) and LeadDrawer.tsx (a lead's people), the
// two places a contact's emails are shown and edited.
import { useState } from "react";
import type { CSSProperties } from "react";
import type { ContactEmail, ContactEmailInput, EmailType } from "../../types/contacts.ts";
import { EMAIL_TYPE_LABELS } from "../../types/contacts.ts";

const EMAIL_TYPE_OPTIONS: EmailType[] = ["home", "work", "linkedin", "other", "custom"];
// Mirrors backend/contacts/contact_emails.py's MAX_EMAILS_PER_REQUEST.
const MAX_EMAILS = 10;

interface Draft {
  address: string;
  email_type: EmailType;
  custom_label: string;
}

const emptyDraft = (): Draft => ({ address: "", email_type: "other", custom_label: "" });

function draftToInput(draft: Draft): ContactEmailInput {
  return {
    address: draft.address.trim(),
    email_type: draft.email_type,
    custom_label: draft.email_type === "custom" ? draft.custom_label.trim() : null,
  };
}

interface ContactEmailsEditorProps {
  emails: ContactEmail[];
  /** False (and no alert shown) keeps the add/edit row open so the user can fix it. */
  onAdd: (input: ContactEmailInput) => Promise<boolean>;
  onReplace: (emailId: number, input: ContactEmailInput) => Promise<boolean>;
  onDelete: (emailId: number) => Promise<boolean>;
  /** Makes aria-labels unique when several editors (one per contact) are on the page at once. */
  idPrefix: string;
}

export default function ContactEmailsEditor({ emails, onAdd, onReplace, onDelete, idPrefix }: ContactEmailsEditorProps) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(emptyDraft());
  const [adding, setAdding] = useState(false);
  const [addDraft, setAddDraft] = useState<Draft>(emptyDraft());
  const [pending, setPending] = useState(false);

  const startEdit = (email: ContactEmail) => {
    setEditingId(email.id);
    setEditDraft({ address: email.address, email_type: email.email_type, custom_label: email.custom_label || "" });
  };

  const saveEdit = async () => {
    if (editingId === null) return;
    const input = draftToInput(editDraft);
    if (!input.address) return;
    setPending(true);
    try {
      if (await onReplace(editingId, input)) setEditingId(null);
    } finally {
      setPending(false);
    }
  };

  const saveAdd = async () => {
    const input = draftToInput(addDraft);
    if (!input.address) return;
    setPending(true);
    try {
      if (await onAdd(input)) {
        setAdding(false);
        setAddDraft(emptyDraft());
      }
    } finally {
      setPending(false);
    }
  };

  const remove = async (email: ContactEmail) => {
    if (!window.confirm(`Remove ${email.address}?`)) return;
    setPending(true);
    try {
      await onDelete(email.id);
    } finally {
      setPending(false);
    }
  };

  const draftFields = (draft: Draft, setDraft: (next: Draft) => void) => (
    <>
      <input
        type="email"
        value={draft.address}
        placeholder="Email address"
        aria-label={`Email address (${idPrefix})`}
        autoFocus
        onChange={(e) => setDraft({ ...draft, address: e.target.value })}
        style={{ flex: 1, minWidth: "180px" }}
      />
      <select
        value={draft.email_type}
        aria-label={`Email type (${idPrefix})`}
        onChange={(e) => setDraft({ ...draft, email_type: e.target.value as EmailType })}
        style={{ width: "auto" }}
      >
        {EMAIL_TYPE_OPTIONS.map((type) => (
          <option key={type} value={type}>
            {EMAIL_TYPE_LABELS[type]}
          </option>
        ))}
      </select>
      {draft.email_type === "custom" && (
        <input
          type="text"
          value={draft.custom_label}
          placeholder="Label (e.g. Work #2)"
          aria-label={`Custom email label (${idPrefix})`}
          maxLength={40}
          onChange={(e) => setDraft({ ...draft, custom_label: e.target.value })}
          style={{ width: "140px" }}
        />
      )}
    </>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      {emails.map((email) =>
        editingId === email.id ? (
          <div key={email.id} style={row}>
            {draftFields(editDraft, setEditDraft)}
            <button type="button" className="btn-primary btn-xs" disabled={pending} onClick={() => void saveEdit()}>
              Save
            </button>
            <button type="button" className="btn-secondary btn-xs" disabled={pending} onClick={() => setEditingId(null)}>
              Cancel
            </button>
          </div>
        ) : (
          <div key={email.id} style={row}>
            <a href={`mailto:${email.address}`}>{email.address}</a>
            <span className="badge badge-gray">
              {email.email_type === "custom" ? email.custom_label : EMAIL_TYPE_LABELS[email.email_type]}
            </span>
            <button type="button" className="link-button" onClick={() => startEdit(email)}>
              Edit
            </button>
            <button type="button" className="link-button" onClick={() => void remove(email)}>
              Remove
            </button>
          </div>
        ),
      )}
      {adding ? (
        <div style={row}>
          {draftFields(addDraft, setAddDraft)}
          <button type="button" className="btn-primary btn-xs" disabled={pending} onClick={() => void saveAdd()}>
            Save
          </button>
          <button
            type="button"
            className="btn-secondary btn-xs"
            disabled={pending}
            onClick={() => {
              setAdding(false);
              setAddDraft(emptyDraft());
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        emails.length < MAX_EMAILS && (
          <button type="button" className="link-button" onClick={() => setAdding(true)}>
            + Add email
          </button>
        )
      )}
    </div>
  );
}

const row: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  fontSize: "13px",
  flexWrap: "wrap",
};
