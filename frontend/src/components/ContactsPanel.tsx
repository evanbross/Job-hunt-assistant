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

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import CompaniesReport from "./CompaniesReport.tsx";
import ContactDetail from "./ContactDetail.tsx";
import ContactsImport from "./ContactsImport.tsx";
import TitlesReport from "./TitlesReport.tsx";
import { formatLocalDate } from "../utils/dateTimeUtils.ts";
import type { Contact, ContactsListResponse } from "../types/contacts.ts";
import type { RecruiterType } from "../types/contacts.ts";

const PAGE_SIZE = 100;

type View = "contacts" | "companies" | "titles";
type EmailFilter = "all" | "emailed" | "not_emailed" | "awaiting" | "replied";
type RecruiterFilter = "all" | "recruiter" | "not_recruiter";
type EmailState = "none" | "awaiting" | "replied";
type SortDir = "asc" | "desc";

const EMAIL_FILTERS: { value: EmailFilter; label: string }[] = [
  { value: "all", label: "All contacts" },
  { value: "emailed", label: "Emailed" },
  { value: "not_emailed", label: "Not emailed" },
  { value: "awaiting", label: "Emailed, awaiting reply" },
  { value: "replied", label: "Replied" },
];

const RECRUITER_FILTERS: { value: RecruiterFilter; label: string }[] = [
  { value: "all", label: "Recruiters and others" },
  { value: "recruiter", label: "Recruiters only" },
  { value: "not_recruiter", label: "Not recruiters" },
];

// ai: claude-sonnet-5 | 2026-09-30
// Matches backend/db/repositories/contacts.py's recruiter_type CHECK
// constraint. NULL ("") means "not a recruiter".
const RECRUITER_TYPES: { value: RecruiterType | ""; label: string }[] = [
  { value: "", label: "Not a recruiter" },
  { value: "unrated", label: "Recruiter (unrated)" },
  { value: "motion", label: "Motion (high-pressure, low-success)" },
  { value: "low_reliability", label: "Low Reliability" },
  { value: "high_reliability", label: "High Reliability" },
  { value: "offshore_spam", label: "Offshore spam" },
];

// ai: claude-sonnet-5 | 2026-09-25
function emailState(contact: Contact): EmailState {
  if (contact.emails.length === 0) return "none";
  return contact.emails.some((email) => email.response_received_at) ? "replied" : "awaiting";
}

// ai: claude-sonnet-5 | 2026-10-02
const EMAIL_STATE_RANK: Record<EmailState, number> = { none: 0, awaiting: 1, replied: 2 };

// Recruiter column sorts by RECRUITER_TYPES' declared order (not-a-recruiter,
// then unrated/motion/low/high/offshore), not raw alphabetical enum text --
// alphabetical would scatter related tiers apart (e.g. "high_reliability"
// and "low_reliability" don't sort next to "motion").
const RECRUITER_RANK: Record<string, number> = Object.fromEntries(
  RECRUITER_TYPES.map((t, i) => [t.value, i]),
);

interface SortColumn {
  key: string;
  label: string;
  getValue: (c: Contact) => number | string;
}

// ai: claude-sonnet-5 | 2026-10-02
// Column sort-key config for the contacts table header. Each getValue()
// return must be directly comparable with < / > (numbers or lowercased
// strings) -- see toggleSort/sortedContacts below.
const SORT_COLUMNS: SortColumn[] = [
  // ai: gpt-5 | 2026-10-03
  { key: "outreach_score", label: "Priority", getValue: (c) => c.outreach_score ?? 0 },
  { key: "interested", label: "Want to Email", getValue: (c) => (c.interested ? 1 : 0) },
  { key: "name", label: "Contact", getValue: (c) => (c.full_name || "").toLowerCase() },
  { key: "recruiter", label: "Recruiter", getValue: (c) => RECRUITER_RANK[c.recruiter_type || ""] ?? 0 },
  { key: "sent_email", label: "Sent Email", getValue: (c) => (c.emails.length > 0 ? 1 : 0) },
  { key: "email_status", label: "Email status", getValue: (c) => EMAIL_STATE_RANK[emailState(c)] },
];

// ai: claude-sonnet-5 | 2026-09-25
// Profile URLs come from the imported CSV, so only http(s) may become a link
// target -- a `javascript:` value in the file must never reach an href.
function safeHttpUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

interface FilterState {
  search: string;
  emailFilter: EmailFilter;
  recruiterFilter: RecruiterFilter;
  interestedOnly: boolean;
  titleFilter: string | null;
}

// ai: claude-sonnet-5 | 2026-09-25
function matchesFilters(contact: Contact, { search, emailFilter, recruiterFilter, interestedOnly, titleFilter }: FilterState): boolean {
  if (interestedOnly && !contact.interested) return false;
  if (titleFilter !== null && contact.normalized_title !== titleFilter) return false;

  const state = emailState(contact);
  if (emailFilter === "emailed" && state === "none") return false;
  if (emailFilter === "not_emailed" && state !== "none") return false;
  if (emailFilter === "awaiting" && state !== "awaiting") return false;
  if (emailFilter === "replied" && state !== "replied") return false;

  if (recruiterFilter === "recruiter" && !contact.recruiter_type) return false;
  if (recruiterFilter === "not_recruiter" && contact.recruiter_type) return false;

  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [contact.full_name, contact.company_raw, contact.position, contact.notes].some((field) =>
    (field || "").toLowerCase().includes(needle),
  );
}

// ai: claude-sonnet-5 | 2026-09-25
async function sendJson(url: string, method: string, body?: unknown): Promise<Response> {
  return fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

type ContactPatchFields = Partial<Pick<Contact, "email_address" | "notes" | "interested" | "recruiter_type">>;

// ai: claude-sonnet-5 | 2026-09-25
export default function ContactsPanel() {
  const [view, setView] = useState<View>("contacts");
  const [titleFilter, setTitleFilter] = useState<string | null>(null); // normalized_title, set by TitlesReport's count link
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [search, setSearch] = useState("");
  const [emailFilter, setEmailFilter] = useState<EmailFilter>("all");
  const [recruiterFilter, setRecruiterFilter] = useState<RecruiterFilter>("all");
  const [interestedOnly, setInterestedOnly] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkRecruiterType, setBulkRecruiterType] = useState<RecruiterType>("unrated");
  // ai: gpt-5 | 2026-10-03
  const [sortKey, setSortKey] = useState("outreach_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const loadContacts = async () => {
    try {
      const res = await fetch("/api/contacts");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ContactsListResponse = await res.json();
      setContacts(data.contacts);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load contacts: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadContacts();
  }, []);

  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [search, emailFilter, recruiterFilter, interestedOnly, titleFilter]);

  const viewContactsForTitle = (normalizedTitle: string) => {
    setTitleFilter(normalizedTitle);
    setView("contacts");
  };

  const filtered = useMemo(
    () =>
      contacts.filter((contact) =>
        matchesFilters(contact, { search, emailFilter, recruiterFilter, interestedOnly, titleFilter }),
      ),
    [contacts, search, emailFilter, recruiterFilter, interestedOnly, titleFilter],
  );
  const interestedCount = useMemo(() => contacts.filter((c) => c.interested).length, [contacts]);

  // ai: claude-sonnet-5 | 2026-10-02
  // Clicking a sortable column header sets it as the sort key; clicking the
  // same header again flips direction instead of re-selecting it.
  const toggleSort = (key: string) => {
    setSortKey((prevKey) => {
      setSortDir((prevDir) => (prevKey === key ? (prevDir === "asc" ? "desc" : "asc") : "asc"));
      return key;
    });
  };

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const column = SORT_COLUMNS.find((c) => c.key === sortKey);
    if (!column) return filtered;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = column.getValue(a);
      const bv = column.getValue(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.full_name.localeCompare(b.full_name) || a.id - b.id;
    });
  }, [filtered, sortKey, sortDir]);

  // ai: claude-sonnet-5 | 2026-09-25
  // A bulk action must only ever touch rows the user can currently see in the
  // filtered list, so selections that a filter change hides are dropped.
  useEffect(() => {
    setSelectedIds((prev) => {
      const filteredIds = new Set(filtered.map((c) => c.id));
      const kept = new Set([...prev].filter((id) => filteredIds.has(id)));
      return kept.size === prev.size ? prev : kept;
    });
  }, [filtered]);

  const allFilteredSelected = filtered.length > 0 && selectedIds.size === filtered.length;

  const toggleSelected = (contactId: number, checked: boolean) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(contactId);
      else next.delete(contactId);
      return next;
    });

  const toggleSelectAllFiltered = (checked: boolean) =>
    setSelectedIds(checked ? new Set(filtered.map((c) => c.id)) : new Set());

  const bulkSetRecruiter = async (recruiterType: RecruiterType | null) => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    setBulkPending(true);
    try {
      const res = await sendJson("/api/contacts/bulk-recruiter", "POST", {
        contact_ids: ids,
        recruiter_type: recruiterType,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const idSet = new Set(ids);
      setContacts((prev) =>
        prev.map((c) => (idSet.has(c.id) ? { ...c, recruiter_type: recruiterType } : c)),
      );
      setSelectedIds(new Set());
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Bulk update failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBulkPending(false);
    }
  };

  const replaceContact = (contactId: number, changes: Partial<Contact>) =>
    setContacts((prev) => prev.map((c) => (c.id === contactId ? { ...c, ...changes } : c)));

  // Optimistic: flip the UI immediately, roll back only the touched fields if
  // the server rejects the write.
  const patchContact = async (contactId: number, fields: ContactPatchFields): Promise<boolean> => {
    const previous = contacts.find((c) => c.id === contactId);
    if (!previous) return false;
    const rollback: ContactPatchFields = Object.fromEntries(
      Object.keys(fields).map((key) => [key, previous[key as keyof ContactPatchFields]]),
    );
    replaceContact(contactId, fields);
    try {
      const res = await sendJson(`/api/contacts/${contactId}`, "PATCH", fields);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
      return true;
    } catch (e) {
      replaceContact(contactId, rollback);
      window.alert(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  };

  const saveEmailAddress = (contactId: number, value: string) =>
    patchContact(contactId, { email_address: value === "" ? null : value });

  const saveNotes = (contactId: number, value: string) =>
    patchContact(contactId, { notes: value === "" ? null : value });

  const logEmail = async (contactId: number, sentAt: string) => {
    try {
      const res = await sendJson(`/api/contacts/${contactId}/emails`, "POST", { sent_at: sentAt });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = await res.json();
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId
            ? {
                ...c,
                emails: [...c.emails, created].sort(
                  (a, b) => a.sent_at.localeCompare(b.sent_at) || a.id - b.id,
                ),
              }
            : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not log the email: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const setResponse = async (contactId: number, emailId: number, receivedAt: string | null) => {
    try {
      const res = await sendJson(`/api/contact-emails/${emailId}`, "PATCH", {
        response_received_at: receivedAt,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId
            ? {
                ...c,
                emails: c.emails.map((email) =>
                  email.id === emailId ? { ...email, response_received_at: receivedAt } : email,
                ),
              }
            : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not update the reply: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const deleteEmail = async (contactId: number, emailId: number) => {
    try {
      const res = await sendJson(`/api/contact-emails/${emailId}`, "DELETE");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContacts((prev) =>
        prev.map((c) =>
          c.id === contactId ? { ...c, emails: c.emails.filter((e) => e.id !== emailId) } : c,
        ),
      );
      // ai: gpt-5 | 2026-10-03
      await loadContacts();
    } catch (e) {
      window.alert(`Could not remove the email: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const importVisible = showImport || (!loading && !loadError && contacts.length === 0);
  const visible = sorted.slice(0, visibleCount);

  return (
    <div>
      <div style={{ marginBottom: "16px", display: "flex", alignItems: "flex-end", gap: "16px" }}>
        <div style={{ flex: 1 }}>
          <h2 style={titleStyle}>Contacts</h2>
          <p style={summaryStyle}>
            Your LinkedIn connections. Check the ones you're interested in reaching, mark the
            recruiters (or select several and mark them at once), and log the emails you send
            and any replies.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            className={view === "contacts" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => setView("contacts")}
          >
            Contacts
          </button>
          <button
            className={view === "companies" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => setView("companies")}
          >
            Companies
          </button>
          <button
            className={view === "titles" ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
            onClick={() => setView("titles")}
          >
            Titles
          </button>
        </div>
        {view === "contacts" && contacts.length > 0 && (
          <button className="btn-secondary btn-sm" onClick={() => setShowImport((open) => !open)}>
            {importVisible ? "Hide import" : "Import CSV"}
          </button>
        )}
      </div>

      {view === "companies" && <CompaniesReport />}
      {view === "titles" && <TitlesReport onViewContactsForTitle={viewContactsForTitle} />}
      {view === "contacts" && (
        <>
          {titleFilter !== null && (
            <div style={titleFilterBar}>
              Filtered to title: <strong>{titleFilter || "(no title)"}</strong>
              <button className="btn-secondary btn-xs" onClick={() => setTitleFilter(null)}>
                Clear
              </button>
            </div>
          )}
          {importVisible && (
            <div className="card" style={{ marginBottom: "16px" }}>
              <ContactsImport onImported={loadContacts} />
            </div>
          )}

          {loadError && <div style={{ color: "var(--red)", marginBottom: "12px" }}>{loadError}</div>}

          {contacts.length > 0 && (
            <>
              <div style={toolbar}>
                <input
                  type="search"
                  placeholder="Search name, company, title, or notes"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  style={{ flex: "1 1 220px", minWidth: "180px" }}
                  aria-label="Search contacts"
                />
                <select
                  value={emailFilter}
                  onChange={(e) => setEmailFilter(e.target.value as EmailFilter)}
                  aria-label="Filter by email status"
                >
                  {EMAIL_FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <select
                  value={recruiterFilter}
                  onChange={(e) => setRecruiterFilter(e.target.value as RecruiterFilter)}
                  aria-label="Filter by recruiter"
                >
                  {RECRUITER_FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
                  <input
                    type="checkbox"
                    checked={interestedOnly}
                    onChange={(e) => setInterestedOnly(e.target.checked)}
                  />
                  Want to email only ({interestedCount})
                </label>
              </div>

              <div style={{ color: "var(--text-muted)", fontSize: "12px", margin: "0 2px 8px" }}>
                {filtered.length.toLocaleString()} of {contacts.length.toLocaleString()} contacts
              </div>

              {selectedIds.size > 0 && (
                <div style={bulkBar}>
                  <strong>{selectedIds.size.toLocaleString()} selected</strong>
                  <select
                    value={bulkRecruiterType}
                    onChange={(e) => setBulkRecruiterType(e.target.value as RecruiterType)}
                    aria-label="Recruiter type to apply to selection"
                  >
                    {RECRUITER_TYPES.filter((option) => option.value !== "").map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn-primary btn-sm"
                    disabled={bulkPending}
                    onClick={() => bulkSetRecruiter(bulkRecruiterType)}
                  >
                    Set recruiter type
                  </button>
                  <button
                    className="btn-secondary btn-sm"
                    disabled={bulkPending}
                    onClick={() => bulkSetRecruiter(null)}
                  >
                    Not a recruiter
                  </button>
                  <button
                    className="btn-secondary btn-sm"
                    disabled={bulkPending}
                    onClick={() => setSelectedIds(new Set())}
                  >
                    Clear selection
                  </button>
                </div>
              )}

              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div style={scrollArea}>
                  {visible.length > 0 && (
                    <div style={{ ...rowGrid, ...headerRow, ...stickyHeader }}>
                      <label style={headerCell}>
                        <input
                          type="checkbox"
                          checked={allFilteredSelected}
                          onChange={(e) => toggleSelectAllFiltered(e.target.checked)}
                          aria-label={`Select all ${filtered.length} filtered contacts`}
                        />
                        Select
                      </label>
                      {SORT_COLUMNS.map((column) => (
                        <button
                          key={column.key}
                          type="button"
                          onClick={() => toggleSort(column.key)}
                          style={sortHeaderCell}
                          aria-label={`Sort by ${column.label}`}
                        >
                          {column.label}
                          {sortKey === column.key && (sortDir === "asc" ? " ▲" : " ▼")}
                        </button>
                      ))}
                      <span />
                    </div>
                  )}
                  {visible.length === 0 ? (
                    <div style={{ padding: "18px", color: "var(--text-muted)" }}>
                      No contacts match these filters.
                    </div>
                  ) : (
                    visible.map((contact) => {
                    const state = emailState(contact);
                    const profileUrl = safeHttpUrl(contact.profile_url);
                    const expanded = expandedId === contact.id;
                    const lastSent = contact.emails.length
                      ? contact.emails[contact.emails.length - 1].sent_at
                      : null;
                    return (
                      <div key={contact.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <div style={rowGrid}>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(contact.id)}
                            onChange={(e) => toggleSelected(contact.id, e.target.checked)}
                            aria-label={`Select ${contact.full_name}`}
                          />
                          {/* ai: gpt-5 | 2026-10-03 */}
                          <div
                            style={{ fontWeight: 700, textAlign: "center" }}
                            title="Weighted outreach priority (company, role, relationship, and engagement)"
                          >
                            {contact.outreach_score?.toFixed(1) ?? "—"}
                          </div>
                          <input
                            type="checkbox"
                            checked={!!contact.interested}
                            onChange={(e) => patchContact(contact.id, { interested: e.target.checked })}
                            aria-label={`Want to email ${contact.full_name}`}
                          />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600 }}>
                              {profileUrl ? (
                                <a href={profileUrl} target="_blank" rel="noopener noreferrer">
                                  {contact.full_name}
                                </a>
                              ) : (
                                contact.full_name
                              )}
                            </div>
                            <div style={subtleText}>
                              {[contact.position, contact.company_raw].filter(Boolean).join(" at ") ||
                                "No title or company"}
                            </div>
                            {contact.notes && (
                              <div style={{ ...subtleText, fontStyle: "italic" }} title={contact.notes}>
                                {contact.notes}
                              </div>
                            )}
                          </div>
                          <select
                            value={contact.recruiter_type || ""}
                            onChange={(e) =>
                              patchContact(contact.id, {
                                recruiter_type: e.target.value === "" ? null : (e.target.value as RecruiterType),
                              })
                            }
                            aria-label={`Recruiter type for ${contact.full_name}`}
                            style={{ fontSize: "12px" }}
                          >
                            {RECRUITER_TYPES.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                          <div
                            style={{ textAlign: "center" }}
                            title={
                              lastSent
                                ? `Last sent ${formatLocalDate(lastSent)}`
                                : "No email logged yet"
                            }
                          >
                            {contact.emails.length > 0 ? (
                              <span aria-label={`Email sent to ${contact.full_name}`} style={{ color: "var(--green)" }}>
                                ✓
                              </span>
                            ) : (
                              <span aria-label={`No email sent to ${contact.full_name}`} style={{ color: "var(--text-muted)" }}>
                                —
                              </span>
                            )}
                          </div>
                          <div>
                            {state === "none" && <span className="badge badge-gray">Not emailed</span>}
                            {state === "awaiting" && (
                              <span className="badge badge-orange">
                                Emailed {formatLocalDate(lastSent)}
                                {contact.emails.length > 1 ? ` (${contact.emails.length}x)` : ""} · no reply
                              </span>
                            )}
                            {state === "replied" && (
                              <span className="badge badge-green">
                                Replied
                                {contact.emails.length > 1 ? ` (${contact.emails.length} sent)` : ""}
                              </span>
                            )}
                          </div>
                          <button
                            className="btn-secondary btn-xs"
                            onClick={() => setExpandedId(expanded ? null : contact.id)}
                            aria-expanded={expanded}
                          >
                            {expanded ? "Close" : "Details"}
                          </button>
                        </div>
                        {expanded && (
                          <ContactDetail
                            contact={contact}
                            onSaveEmailAddress={saveEmailAddress}
                            onSaveNotes={saveNotes}
                            onLogEmail={logEmail}
                            onSetResponse={setResponse}
                            onDeleteEmail={deleteEmail}
                          />
                        )}
                      </div>
                    );
                  })
                )}
                </div>
              </div>

              {filtered.length > visibleCount && (
                <div style={{ textAlign: "center", marginTop: "12px" }}>
                  <button
                    className="btn-secondary btn-sm"
                    onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
                  >
                    Show {Math.min(PAGE_SIZE, filtered.length - visibleCount)} more
                  </button>
                </div>
              )}
            </>
          )}

          {loading && <div style={{ color: "var(--text-muted)" }}>Loading contacts…</div>}
        </>
      )}
    </div>
  );
}

const titleStyle: CSSProperties = {
  fontSize: "18px",
  fontWeight: 700,
  lineHeight: 1.2,
};

const summaryStyle: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginTop: "2px",
  maxWidth: "640px",
};

const toolbar: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "10px",
  marginBottom: "10px",
};

const rowGrid: CSSProperties = {
  display: "grid",
  // ai: gpt-5 | 2026-10-03
  gridTemplateColumns: "64px 72px 90px minmax(0, 1fr) 150px 90px 230px 70px",
  alignItems: "center",
  gap: "12px",
  padding: "10px 14px",
};

// ai: claude-sonnet-5 | 2026-10-02
// Keeps the header row fixed at the top of its own scroll container while
// the contact rows scroll underneath it. Needs a solid background (inherited
// from headerRow) so scrolled-under rows don't show through.
const scrollArea: CSSProperties = {
  maxHeight: "65vh",
  overflowY: "auto",
  overflowX: "hidden",
};

const stickyHeader: CSSProperties = {
  position: "sticky",
  top: 0,
  zIndex: 1,
};

const bulkBar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "8px 14px",
  marginBottom: "10px",
  background: "var(--accent-light)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: "13px",
};

const titleFilterBar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "8px 14px",
  marginBottom: "10px",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: "13px",
};

const headerRow: CSSProperties = {
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
  padding: "6px 14px",
};

const headerCell: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  fontSize: "11px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

// ai: claude-sonnet-5 | 2026-10-02 — sortable column header, styled to match
// headerCell but as a real <button> so it's keyboard/focus accessible.
const sortHeaderCell: CSSProperties = {
  ...headerCell,
  background: "none",
  border: "none",
  padding: 0,
  margin: 0,
  cursor: "pointer",
  textAlign: "left",
  fontFamily: "inherit",
};

const subtleText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};
