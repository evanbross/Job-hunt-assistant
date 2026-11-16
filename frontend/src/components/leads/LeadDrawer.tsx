/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// One lead in full (GET /api/leads/<id>): its details and follow-up date, its stage
// (with a note in the user's own words), the people on it, the call/message/note
// timeline, a rich-text running summary, and the job description.
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import JobSightings from "../common/JobSightings.tsx";
import Modal from "../common/Modal.tsx";
import RichTextEditor from "../common/RichTextEditor.tsx";
import RichTextView from "../common/RichTextView.tsx";
import { isRichTextEmpty } from "../../utils/richText.ts";
import type { ApplicationStageStatus } from "../../types/applications.ts";
import type { Contact, ContactsListResponse } from "../../types/contacts.ts";
import type { ContactRole, Interaction, LeadDetail, LeadPerson } from "../../types/leads.ts";
import { CHANNEL_LABELS, KIND_LABELS, ORIGIN_LABELS, ROLE_LABELS } from "../../types/leads.ts";
import { responseErrorText, safeHttpUrl, sendJson } from "../../utils/contactsUtils.ts";
import { formatLocalDate, formatLocalDateTime } from "../../utils/dateTimeUtils.ts";
import { ContactPicker, NewContactFields } from "./ContactFields.tsx";
import InteractionModal from "./InteractionModal.tsx";
import { emptyNewContact, getJson, localToday, newContactBody, stageLabel } from "./leadsShared.ts";

// ai: claude-opus-5-5 | 2026-11-11 -- phase 2
const CallNotesModal = lazy(() => import("./CallNotesModal.tsx"));

interface LeadDrawerProps {
  jobId: number;
  catalog: ApplicationStageStatus[];
  /** Contacts to pick from when adding a person; loaded here when not given. */
  contacts?: Contact[];
  /** Something about the lead changed: the board should reload. */
  onChanged: () => void;
  onClose: () => void;
}

type TimelineEntry =
  | { type: "interaction"; sortKey: string; interaction: Interaction }
  | { type: "stage"; sortKey: string; status: string; date: string; notes: string | null };

interface HeaderDraft {
  company: string;
  job_title: string;
  url: string;
  next_follow_up: string;
}

export default function LeadDrawer({ jobId, catalog, contacts, onChanged, onClose }: LeadDrawerProps) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [header, setHeader] = useState<HeaderDraft>({ company: "", job_title: "", url: "", next_follow_up: "" });
  const [summaryDraft, setSummaryDraft] = useState("");
  const [summaryEditorKey, setSummaryEditorKey] = useState(0);
  const [editingSummary, setEditingSummary] = useState(false);
  const [jdDraft, setJdDraft] = useState("");
  const [stageKey, setStageKey] = useState("");
  const [stageDate, setStageDate] = useState(localToday());
  const [stageNote, setStageNote] = useState("");
  const [addingPerson, setAddingPerson] = useState(false);
  const [personMode, setPersonMode] = useState<"existing" | "new">("existing");
  const [personId, setPersonId] = useState<number | null>(null);
  const [newPerson, setNewPerson] = useState(emptyNewContact());
  const [personRole, setPersonRole] = useState<ContactRole>("agency_recruiter");
  const [interactionModal, setInteractionModal] = useState<{ existing: Interaction | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [importingNotes, setImportingNotes] = useState(false); // ai: claude-opus-5-5 | 2026-11-11
  const [loadedContacts, setLoadedContacts] = useState<Contact[]>([]);

  const load = useCallback(async () => {
    try {
      const loaded = await getJson<LeadDetail>(`/api/leads/${jobId}`);
      setDetail(loaded);
      setLoadError(null);
      setHeader({
        company: loaded.lead.company || "",
        job_title: loaded.lead.job_title || "",
        url: loaded.lead.url || "",
        next_follow_up: loaded.lead.next_follow_up || "",
      });
      setSummaryDraft(loaded.lead.summary_html || "");
      setJdDraft(loaded.lead.job_description || "");
      setStageKey(loaded.lead.stage);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Opened from somewhere that didn't load Contacts (the Applications sheet): fetch
  // them only once the user starts adding a person.
  useEffect(() => {
    if (contacts !== undefined || !addingPerson) return;
    let cancelled = false;
    getJson<ContactsListResponse>("/api/contacts")
      .then((body) => {
        if (!cancelled) setLoadedContacts(body.contacts);
      })
      .catch(() => {
        if (!cancelled) setLoadedContacts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [contacts, addingPerson]);

  const timeline = useMemo<TimelineEntry[]>(() => {
    if (!detail) return [];
    const entries: TimelineEntry[] = [
      ...detail.interactions.map((interaction) => ({
        type: "interaction" as const,
        sortKey: interaction.occurred_at,
        interaction,
      })),
      ...detail.stages.map((stage) => ({
        type: "stage" as const,
        // A stage date is a plain day: sort it at the start of that day.
        sortKey: `${stage.date}T00:00:00`,
        status: stage.status,
        date: stage.date,
        notes: stage.notes,
      })),
    ];
    return entries.sort((a, b) => b.sortKey.localeCompare(a.sortKey));
  }, [detail]);

  const linkedIds = useMemo(() => new Set(detail?.people.map((p) => p.id) || []), [detail]);
  const pickable = useMemo(
    () => (contacts ?? loadedContacts).filter((c) => !linkedIds.has(c.id)),
    [contacts, loadedContacts, linkedIds],
  );

  // Runs one write, then reloads the lead (and tells the board) only when it worked.
  const write = async (action: () => Promise<Response>, failure: string): Promise<boolean> => {
    setBusy(true);
    try {
      const res = await action();
      if (!res.ok) {
        window.alert(`${failure}: ${await responseErrorText(res)}`);
        return false;
      }
      await load();
      onChanged();
      return true;
    } catch (e) {
      window.alert(`${failure}: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <Modal title="Lead" onClose={onClose}>
        <p role="alert">Could not load this lead: {loadError}</p>
      </Modal>
    );
  }
  if (!detail) {
    return (
      <Modal title="Lead" onClose={onClose}>
        <p>Loading…</p>
      </Modal>
    );
  }

  const { lead } = detail;
  const saveHeader = () => {
    if (!header.company.trim()) {
      window.alert("Company can't be empty (use “Undisclosed” while an agency keeps it back).");
      return;
    }
    void write(
      () =>
        sendJson(`/api/leads/${jobId}`, "PATCH", {
          company: header.company.trim(),
          job_title: header.job_title.trim(),
          url: header.url.trim() || null,
          next_follow_up: header.next_follow_up || null,
        }),
      "Could not save",
    );
  };
  const saveSummary = async () => {
    const saved = await write(
      () => sendJson(`/api/leads/${jobId}`, "PATCH", { summary_html: isRichTextEmpty(summaryDraft) ? null : summaryDraft }),
      "Could not save the summary",
    );
    if (saved) setEditingSummary(false);
  };
  const saveJobDescription = () =>
    void write(
      () => sendJson(`/api/leads/${jobId}`, "PATCH", { job_description: jdDraft.trim() || null }),
      "Could not save the job description",
    );
  const addStage = async () => {
    if (!stageKey) return;
    const saved = await write(
      () =>
        sendJson(`/api/applications/${jobId}/stages`, "POST", {
          status: stageKey,
          date: stageDate || localToday(),
          notes: stageNote.trim() || null,
        }),
      "Could not update the stage",
    );
    if (saved) setStageNote("");
  };
  const addPerson = async () => {
    let contactId = personId;
    if (personMode === "new") {
      if (!newPerson.full_name.trim()) {
        window.alert("Enter their name.");
        return;
      }
      const res = await sendJson("/api/contacts", "POST", newContactBody(newPerson));
      if (res.status === 409) {
        contactId = ((await res.json()) as { contact_id: number }).contact_id;
      } else if (!res.ok) {
        window.alert(`Could not add the contact: ${await responseErrorText(res)}`);
        return;
      } else {
        contactId = ((await res.json()) as { id: number }).id;
      }
    }
    if (contactId === null) {
      window.alert("Choose someone from your contacts.");
      return;
    }
    const saved = await write(
      () =>
        sendJson(`/api/leads/${jobId}/contacts`, "POST", {
          contact_id: contactId,
          role: personRole,
          is_primary: detail.people.length === 0,
        }),
      "Could not add them",
    );
    if (saved) {
      setAddingPerson(false);
      setPersonId(null);
      setNewPerson(emptyNewContact());
    }
  };
  const makePrimary = (person: LeadPerson) =>
    void write(
      () => sendJson(`/api/leads/${jobId}/contacts`, "POST", { contact_id: person.id, role: person.role, is_primary: true }),
      "Could not change the primary contact",
    );
  const removePerson = (person: LeadPerson) => {
    if (!window.confirm(`Remove ${person.full_name} from this lead? Their logged calls and messages stay.`)) return;
    void write(() => sendJson(`/api/leads/${jobId}/contacts/${person.id}`, "DELETE"), "Could not remove them");
  };
  const deleteInteraction = (interaction: Interaction) => {
    if (!window.confirm("Delete this entry?")) return;
    void write(() => sendJson(`/api/interactions/${interaction.id}`, "DELETE"), "Could not delete it");
  };

  const postingUrl = safeHttpUrl(lead.url);
  return (
    <Modal
      title={`${lead.company || "Unknown company"}${lead.job_title ? ` — ${lead.job_title}` : ""}`}
      subtitle={`${ORIGIN_LABELS[lead.origin]} · ${stageLabel(catalog, lead.stage)}${lead.date_found ? ` · since ${formatLocalDate(lead.date_found)}` : ""}`}
      onClose={onClose}
      maxWidth="960px"
    >
      <div className="lead-drawer-grid">
        <label>
          Hiring company
          <input type="text" value={header.company} onChange={(e) => setHeader({ ...header, company: e.target.value })} />
        </label>
        <label>
          Role
          <input type="text" value={header.job_title} onChange={(e) => setHeader({ ...header, job_title: e.target.value })} />
        </label>
        <label>
          Posting link {postingUrl && <a href={postingUrl} target="_blank" rel="noopener noreferrer">open</a>}
          <input type="url" value={header.url} onChange={(e) => setHeader({ ...header, url: e.target.value })} />
        </label>
        <label>
          Follow up on
          <input type="date" value={header.next_follow_up} onChange={(e) => setHeader({ ...header, next_follow_up: e.target.value })} />
        </label>
      </div>
      <button type="button" className="btn-secondary" onClick={saveHeader} disabled={busy}>
        Save details
      </button>

      <section className="lead-drawer-section">
        <h4>Stage</h4>
        <div className="form-row">
          <label className="lead-sub">
            New stage
            <select value={stageKey} onChange={(e) => setStageKey(e.target.value)}>
              {catalog.map((s) => (
                <option key={s.key} value={s.key}>{s.label}{s.is_terminal ? " (closes the lead)" : ""}</option>
              ))}
            </select>
          </label>
          <label className="lead-sub">
            Date
            <input type="date" value={stageDate} onChange={(e) => setStageDate(e.target.value)} />
          </label>
          <label className="lead-sub" style={{ gridColumn: "span 2" }}>
            In your own words (optional)
            <input
              type="text"
              value={stageNote}
              placeholder="e.g. Submitted at 95/hr, waiting on the hiring manager"
              onChange={(e) => setStageNote(e.target.value)}
            />
          </label>
        </div>
        <button type="button" className="btn-primary" style={{ marginTop: 8 }} onClick={addStage} disabled={busy}>
          Record stage
        </button>
      </section>

      <section className="lead-drawer-section">
        <h4>People</h4>
        <div className="lead-people">
          {detail.people.length === 0 && <span className="lead-sub">No one attached yet.</span>}
          {detail.people.map((person) => {
            const profile = safeHttpUrl(person.profile_url);
            return (
              <div key={person.id} className="lead-person">
                <strong>{person.full_name}</strong>
                <span className="badge badge-gray">{ROLE_LABELS[person.role]}</span>
                {person.is_primary && <span className="badge badge-accent">Primary</span>}
                {person.keep_informed && <span className="badge badge-yellow">Keep informed</span>}
                <span className="lead-sub">
                  {[person.position, person.company_raw, person.phone, person.email_address].filter(Boolean).join(" · ")}
                </span>
                {profile && <a href={profile} target="_blank" rel="noopener noreferrer">LinkedIn</a>}
                {!person.is_primary && (
                  <button type="button" className="btn-secondary" onClick={() => makePrimary(person)} disabled={busy}>
                    Make primary
                  </button>
                )}
                <button type="button" className="btn-secondary" onClick={() => removePerson(person)} disabled={busy}>
                  Remove
                </button>
              </div>
            );
          })}
        </div>
        {addingPerson ? (
          <div className="form-stack" style={{ marginTop: 8 }}>
            <div style={{ display: "flex", gap: 12 }}>
              <label style={{ flexDirection: "row", gap: 6 }}>
                <input type="radio" checked={personMode === "existing"} onChange={() => setPersonMode("existing")} />
                From my contacts
              </label>
              <label style={{ flexDirection: "row", gap: 6 }}>
                <input type="radio" checked={personMode === "new"} onChange={() => setPersonMode("new")} />
                New contact
              </label>
            </div>
            {personMode === "existing" ? (
              <ContactPicker contacts={pickable} selectedId={personId} onSelect={setPersonId} />
            ) : (
              <NewContactFields value={newPerson} onChange={setNewPerson} />
            )}
            <label className="lead-sub">
              Their role
              <select value={personRole} onChange={(e) => setPersonRole(e.target.value as ContactRole)}>
                {(Object.keys(ROLE_LABELS) as ContactRole[]).map((r) => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn-primary" onClick={addPerson} disabled={busy}>Add</button>
              <button type="button" className="btn-secondary" onClick={() => setAddingPerson(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <button type="button" className="btn-secondary" style={{ marginTop: 8 }} onClick={() => setAddingPerson(true)}>
            Add a person
          </button>
        )}
      </section>

      <section className="lead-drawer-section">
        <h4>Summary</h4>
        {editingSummary ? (
          <>
            <RichTextEditor
              key={summaryEditorKey}
              initialHtml={lead.summary_html}
              onChange={setSummaryDraft}
              ariaLabel="Lead summary"
              placeholder="What you know so far: rate, client, team, what they're looking for…"
            />
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button type="button" className="btn-primary" onClick={saveSummary} disabled={busy}>Save summary</button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setSummaryDraft(lead.summary_html || "");
                  setSummaryEditorKey((k) => k + 1);
                  setEditingSummary(false);
                }}
              >
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <RichTextView html={lead.summary_html} emptyText="No summary yet." />
            <button type="button" className="btn-secondary" style={{ marginTop: 8 }} onClick={() => setEditingSummary(true)}>
              Edit summary
            </button>
          </>
        )}
      </section>

      <section className="lead-drawer-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h4>Timeline</h4>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn-secondary" onClick={() => setImportingNotes(true)}>
              Import call notes
            </button>
            <button type="button" className="btn-primary" onClick={() => setInteractionModal({ existing: null })}>
              Log call / message / note
            </button>
          </div>
        </div>
        <ul className="lead-timeline">
          {timeline.length === 0 && <li className="lead-sub">Nothing logged yet.</li>}
          {timeline.map((entry) =>
            entry.type === "stage" ? (
              <li key={`stage-${entry.sortKey}-${entry.status}`} className="stage-entry">
                <div className="lead-timeline-meta">
                  <span>{formatLocalDate(entry.date)}</span>
                  <strong>Stage: {stageLabel(catalog, entry.status)}</strong>
                </div>
                {entry.notes && <div>{entry.notes}</div>}
              </li>
            ) : (
              <li key={`interaction-${entry.interaction.id}`}>
                <div className="lead-timeline-meta">
                  <span>{formatLocalDateTime(entry.interaction.occurred_at)}</span>
                  <strong>
                    {KIND_LABELS[entry.interaction.kind]} · {CHANNEL_LABELS[entry.interaction.channel]}
                  </strong>
                  <span>
                    {entry.interaction.direction === "inbound" ? "from" : "to"} {entry.interaction.contact_name || "—"}
                  </span>
                  <button type="button" className="btn-secondary" onClick={() => setInteractionModal({ existing: entry.interaction })}>
                    Edit
                  </button>
                  <button type="button" className="btn-secondary" onClick={() => deleteInteraction(entry.interaction)} disabled={busy}>
                    Delete
                  </button>
                </div>
                {entry.interaction.summary && <div style={{ fontWeight: 600 }}>{entry.interaction.summary}</div>}
                <RichTextView html={entry.interaction.body_html} />
              </li>
            ),
          )}
        </ul>
      </section>

      {/* ai: claude-opus-5-5 | 2026-11-15 */}
      {detail.sightings.length > 0 && (
        <section className="lead-drawer-section">
          <h4>Where else this job turned up</h4>
          <JobSightings jobId={jobId} sightings={detail.sightings} />
        </section>
      )}

      <section className="lead-drawer-section">
        <h4>Job description</h4>
        <textarea rows={6} style={{ width: "100%" }} value={jdDraft} onChange={(e) => setJdDraft(e.target.value)} />
        <button type="button" className="btn-secondary" style={{ marginTop: 8 }} onClick={saveJobDescription} disabled={busy}>
          Save job description
        </button>
      </section>

      {importingNotes && (
        <Suspense fallback={null}>
          <CallNotesModal
            lead={lead}
            people={detail.people.map((p) => ({ id: p.id, name: p.full_name }))}
            onChanged={() => {
              void load();
              onChanged();
            }}
            onClose={() => setImportingNotes(false)}
          />
        </Suspense>
      )}
      {interactionModal && (
        <InteractionModal
          jobId={jobId}
          people={detail.people.map((p) => ({ id: p.id, name: p.full_name }))}
          existing={interactionModal.existing}
          onClose={() => setInteractionModal(null)}
          onSaved={() => {
            setInteractionModal(null);
            void load();
            onChanged();
          }}
        />
      )}
    </Modal>
  );
}
