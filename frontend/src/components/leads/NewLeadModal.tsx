/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Create a recruiter lead (POST /api/leads): the role, where it stands, the recruiter
// (from Contacts or typed in) and, optionally, the first call or message.
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import RichTextEditor from "../common/RichTextEditor.tsx";
import { isRichTextEmpty } from "../../utils/richText.ts";
import type { ApplicationStageStatus } from "../../types/applications.ts";
import type { Contact } from "../../types/contacts.ts";
import type { ContactRole, InteractionChannel, InteractionKind } from "../../types/leads.ts";
import { CHANNEL_LABELS, KIND_LABELS, ROLE_LABELS } from "../../types/leads.ts";
import { sendJson, responseErrorText } from "../../utils/contactsUtils.ts";
import { ContactPicker, NewContactFields } from "./ContactFields.tsx";
import { emptyNewContact, localToday, newContactBody } from "./leadsShared.ts";

const UNDISCLOSED_COMPANY = "Undisclosed";
const DEFAULT_STAGE = "recruiter_reached_out";

type RecruiterMode = "existing" | "new" | "none";

interface NewLeadModalProps {
  contacts: Contact[];
  catalog: ApplicationStageStatus[];
  /** Start with this contact chosen (e.g. "Create lead" from an open conversation). */
  presetContactId?: number | null;
  onCreated: (jobId: number) => void;
  /** The URL is already tracked: open that job instead, to attach the recruiter there. */
  onOpenExisting: (jobId: number) => void;
  onClose: () => void;
}

export default function NewLeadModal({
  contacts,
  catalog,
  presetContactId = null,
  onCreated,
  onOpenExisting,
  onClose,
}: NewLeadModalProps) {
  const [company, setCompany] = useState("");
  const [undisclosed, setUndisclosed] = useState(false);
  const [jobTitle, setJobTitle] = useState("");
  const [url, setUrl] = useState("");
  const [stage, setStage] = useState(DEFAULT_STAGE);
  const [stageDate, setStageDate] = useState(localToday());
  const [followUp, setFollowUp] = useState("");
  const [mode, setMode] = useState<RecruiterMode>(presetContactId !== null ? "existing" : "new");
  const [contactId, setContactId] = useState<number | null>(presetContactId);
  const [newContact, setNewContact] = useState(emptyNewContact());
  const [role, setRole] = useState<ContactRole>("agency_recruiter");
  const [logFirst, setLogFirst] = useState(true);
  const [channel, setChannel] = useState<InteractionChannel>("phone");
  const [kind, setKind] = useState<InteractionKind>("call");
  const [callSummary, setCallSummary] = useState("");
  const [callNotes, setCallNotes] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existingJobId, setExistingJobId] = useState<number | null>(null);

  const openStages = catalog.filter((s) => !s.is_terminal);

  const save = async () => {
    const companyValue = undisclosed ? UNDISCLOSED_COMPANY : company.trim();
    if (!companyValue) {
      setError("Enter the hiring company, or tick “Not disclosed yet”.");
      return;
    }
    if (mode === "existing" && contactId === null) {
      setError("Choose the recruiter from your contacts, or add them as a new contact.");
      return;
    }
    if (mode === "new" && !newContact.full_name.trim()) {
      setError("Enter the recruiter's name.");
      return;
    }
    const body: Record<string, unknown> = {
      company: companyValue,
      job_title: jobTitle.trim(),
      stage,
      stage_date: stageDate || localToday(),
      contact_role: role,
    };
    if (url.trim()) body.url = url.trim();
    if (followUp) body.next_follow_up = followUp;
    if (jobDescription.trim()) body.job_description = jobDescription.trim();
    if (mode === "existing") body.contact_id = contactId;
    if (mode === "new") body.new_contact = newContactBody(newContact);
    if (logFirst) {
      body.first_interaction = {
        channel,
        kind,
        direction: "inbound",
        summary: callSummary.trim() || null,
        body_html: isRichTextEmpty(callNotes) ? null : callNotes,
      };
    }
    setSaving(true);
    setError(null);
    setExistingJobId(null);
    try {
      const res = await sendJson("/api/leads", "POST", body);
      if (res.status === 409) {
        const conflict = (await res.json()) as { error?: string; existing_job_id?: number | null };
        setError(conflict.error || "This job is already tracked.");
        setExistingJobId(conflict.existing_job_id ?? null);
        return;
      }
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      const created = (await res.json()) as { id: number };
      onCreated(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="New recruiter lead"
      subtitle="A role a recruiter contacted you about"
      onClose={onClose}
      maxWidth="820px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Create lead"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <div className="form-row">
          <label>
            Hiring company
            <input
              type="text"
              value={undisclosed ? UNDISCLOSED_COMPANY : company}
              disabled={undisclosed}
              onChange={(event) => setCompany(event.target.value)}
            />
          </label>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "end" }}>
            <input type="checkbox" checked={undisclosed} onChange={(event) => setUndisclosed(event.target.checked)} />
            Not disclosed yet
          </label>
          <label>
            Role
            <input type="text" value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} />
          </label>
        </div>
        <div className="form-row">
          <label>
            Stage
            <select value={stage} onChange={(event) => setStage(event.target.value)}>
              {openStages.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </label>
          <label>
            Since
            <input type="date" value={stageDate} onChange={(event) => setStageDate(event.target.value)} />
          </label>
          <label>
            Follow up on
            <input type="date" value={followUp} onChange={(event) => setFollowUp(event.target.value)} />
          </label>
          <label>
            Posting link (optional)
            <input type="url" value={url} placeholder="https://…" onChange={(event) => setUrl(event.target.value)} />
          </label>
        </div>

        <fieldset className="form-stack" style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
          <legend style={{ fontWeight: 600, fontSize: 13 }}>Recruiter</legend>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {(["new", "existing", "none"] as const).map((option) => (
              <label key={option} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <input type="radio" name="recruiter-mode" checked={mode === option} onChange={() => setMode(option)} />
                {option === "new" ? "New contact" : option === "existing" ? "From my contacts" : "Add later"}
              </label>
            ))}
          </div>
          {mode === "existing" && <ContactPicker contacts={contacts} selectedId={contactId} onSelect={setContactId} />}
          {mode === "new" && <NewContactFields value={newContact} onChange={setNewContact} />}
          {mode !== "none" && (
            <label>
              Their role
              <select value={role} onChange={(event) => setRole(event.target.value as ContactRole)}>
                {(Object.keys(ROLE_LABELS) as ContactRole[]).map((r) => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
            </label>
          )}
        </fieldset>

        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={logFirst} onChange={(event) => setLogFirst(event.target.checked)} />
          Log how they reached out
        </label>
        {logFirst && (
          <div className="form-stack">
            <div className="form-row">
              <label>
                What
                <select value={kind} onChange={(event) => setKind(event.target.value as InteractionKind)}>
                  {(Object.keys(KIND_LABELS) as InteractionKind[]).map((k) => (
                    <option key={k} value={k}>{KIND_LABELS[k]}</option>
                  ))}
                </select>
              </label>
              <label>
                How
                <select value={channel} onChange={(event) => setChannel(event.target.value as InteractionChannel)}>
                  {(Object.keys(CHANNEL_LABELS) as InteractionChannel[]).map((c) => (
                    <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              One-line summary
              <input type="text" value={callSummary} maxLength={1000} onChange={(event) => setCallSummary(event.target.value)} />
            </label>
            <RichTextEditor initialHtml="" onChange={setCallNotes} ariaLabel="Call notes" placeholder="What did they say about the role?" />
          </div>
        )}

        <label>
          Job description (optional — paste what they sent)
          <textarea rows={4} value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} />
        </label>

        {error && (
          <div role="alert" style={{ color: "var(--red)" }}>
            {error}
            {existingJobId !== null && (
              <>
                {" "}
                <button type="button" className="btn-secondary" onClick={() => onOpenExisting(existingJobId)}>
                  Open that job
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
