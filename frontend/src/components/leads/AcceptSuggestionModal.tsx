/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Accept a suggested lead: the sender becomes a recruiter contact with the
// conversation on their timeline, and optionally a new lead or a place on one the
// user already tracks. The AI's guesses only pre-fill the fields.
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import type { AcceptSuggestionBody, AcceptSuggestionResponse, LeadSuggestion, LeadSummary } from "../../types/leads.ts";
import { responseErrorText, sendJson } from "../../utils/contactsUtils.ts";

type LeadMode = AcceptSuggestionBody["lead"];

interface AcceptSuggestionModalProps {
  suggestion: LeadSuggestion;
  leads: LeadSummary[];
  onAccepted: (result: AcceptSuggestionResponse) => void;
  onClose: () => void;
}

export default function AcceptSuggestionModal({ suggestion, leads, onAccepted, onClose }: AcceptSuggestionModalProps) {
  const openLeads = leads.filter((lead) => !lead.is_closed);
  const [mode, setMode] = useState<LeadMode>("new");
  const [company, setCompany] = useState(suggestion.hiring_company_guess ?? "");
  const [jobTitle, setJobTitle] = useState(suggestion.role_guess ?? "");
  const [jobId, setJobId] = useState<number | null>(openLeads[0]?.id ?? null);
  const [contactCompany, setContactCompany] = useState(suggestion.recruiter_company_guess ?? "");
  const [contactPosition, setContactPosition] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (mode === "existing" && jobId === null) {
      setError("Pick the lead to add them to.");
      return;
    }
    const body: AcceptSuggestionBody = { lead: mode };
    if (mode === "new") Object.assign(body, { company, job_title: jobTitle });
    if (mode === "existing" && jobId !== null) body.job_id = jobId;
    if (suggestion.contact_id === null) Object.assign(body, { contact_company: contactCompany, contact_position: contactPosition });
    setBusy(true);
    setError(null);
    try {
      const res = await sendJson(`/api/lead-suggestions/${suggestion.id}/accept`, "POST", body);
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      onAccepted((await res.json()) as AcceptSuggestionResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const modeOption = (value: LeadMode, label: string, disabled = false) => (
    <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
      <input type="radio" name="lead-mode" checked={mode === value} disabled={disabled} onChange={() => setMode(value)} />
      {label}
    </label>
  );

  return (
    <Modal
      title={`Accept ${suggestion.counterpart_name}`}
      subtitle={`Their ${suggestion.message_count} message${suggestion.message_count === 1 ? "" : "s"} go on the timeline`}
      onClose={onClose}
      maxWidth="640px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Accept"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        {suggestion.contact_id === null && (
          <div className="form-row">
            <label>
              Their company or agency
              <input value={contactCompany} onChange={(e) => setContactCompany(e.target.value)} />
            </label>
            <label>
              Their title
              <input value={contactPosition} placeholder="Recruiter" onChange={(e) => setContactPosition(e.target.value)} />
            </label>
          </div>
        )}
        <fieldset style={{ border: "none", padding: 0, margin: 0 }} className="form-stack">
          <legend>Lead</legend>
          {modeOption("new", "Create a lead")}
          {modeOption("existing", "Add to a lead I already track", openLeads.length === 0)}
          {modeOption("none", "Just add the contact and conversation")}
        </fieldset>
        {mode === "new" && (
          <div className="form-row">
            <label>
              Hiring company
              <input value={company} placeholder="Undisclosed" onChange={(e) => setCompany(e.target.value)} />
            </label>
            <label>
              Role
              <input value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} />
            </label>
          </div>
        )}
        {mode === "existing" && (
          <label>
            Which lead
            <select value={jobId ?? ""} onChange={(e) => setJobId(e.target.value ? Number(e.target.value) : null)}>
              {openLeads.map((lead) => (
                <option key={lead.id} value={lead.id}>
                  {lead.company}{lead.job_title ? ` — ${lead.job_title}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
      </div>
    </Modal>
  );
}
