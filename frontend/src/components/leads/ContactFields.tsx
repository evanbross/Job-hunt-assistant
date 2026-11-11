/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Choosing the person behind a lead: someone already in Contacts, or a new contact
// typed in (a recruiter who called or emailed and isn't a LinkedIn connection).
import { useMemo, useState } from "react";
import type { Contact } from "../../types/contacts.ts";
import type { NewContactDraft } from "./leadsShared.ts";

const MAX_MATCHES = 8;

interface NewContactFieldsProps {
  value: NewContactDraft;
  onChange: (next: NewContactDraft) => void;
}

export function NewContactFields({ value, onChange }: NewContactFieldsProps) {
  const field = (key: keyof NewContactDraft, label: string, type = "text", placeholder = "") => (
    <label>
      {label}
      <input
        type={type}
        value={value[key]}
        placeholder={placeholder}
        onChange={(event) => onChange({ ...value, [key]: event.target.value })}
      />
    </label>
  );
  return (
    <div className="form-row">
      {field("full_name", "Name *")}
      {field("company", "Their company (agency)")}
      {field("position", "Title", "text", "Technical Recruiter")}
      {field("phone", "Phone", "tel")}
      {field("email_address", "Email", "email")}
      {field("profile_url", "LinkedIn profile", "url", "https://www.linkedin.com/in/…")}
    </div>
  );
}

interface ContactPickerProps {
  contacts: Contact[];
  selectedId: number | null;
  onSelect: (contactId: number | null) => void;
}

/** Search Contacts by name or company and pick one. */
export function ContactPicker({ contacts, selectedId, onSelect }: ContactPickerProps) {
  const [query, setQuery] = useState("");
  const selected = contacts.find((c) => c.id === selectedId) || null;
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return contacts
      .filter((c) => `${c.full_name} ${c.company_raw}`.toLowerCase().includes(needle))
      .slice(0, MAX_MATCHES);
  }, [contacts, query]);

  if (selected) {
    return (
      <div className="lead-person">
        <strong>{selected.full_name}</strong>
        <span className="lead-sub">{[selected.position, selected.company_raw].filter(Boolean).join(" · ")}</span>
        <button type="button" className="btn-secondary" onClick={() => onSelect(null)}>
          Change
        </button>
      </div>
    );
  }
  return (
    <div className="form-stack">
      <input
        type="search"
        value={query}
        placeholder="Search your contacts by name or company"
        aria-label="Search contacts"
        onChange={(event) => setQuery(event.target.value)}
      />
      {query.trim() !== "" && matches.length === 0 && <span className="lead-sub">No matching contacts.</span>}
      {matches.map((c) => (
        <button key={c.id} type="button" className="btn-secondary" style={{ textAlign: "left" }} onClick={() => onSelect(c.id)}>
          {c.full_name}
          <span className="lead-sub">{[c.position, c.company_raw].filter(Boolean).join(" · ")}</span>
        </button>
      ))}
    </div>
  );
}
