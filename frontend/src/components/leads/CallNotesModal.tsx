/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Import call notes into a lead (recruiter lead tracking, phase 2): paste or upload a
// transcript / note-taker summary, which is saved verbatim on the timeline; then
// review what the AI read out of it and apply only the changes ticked here.
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import type { CallNotesImportResponse, CallNotesProposal, InteractionChannel, LeadDetail } from "../../types/leads.ts";
import { CHANNEL_LABELS } from "../../types/leads.ts";
import { responseErrorText, sendJson } from "../../utils/contactsUtils.ts";
import { formatLocalDate, localInputToUtcIso, toLocalDateTimeInputValue } from "../../utils/dateTimeUtils.ts";
import { appendedSummaryHtml, proposalSummaryItems } from "./leadsShared.ts";

const ACCEPTED_FILES = ".txt,.md,.vtt,.docx";

interface CallNotesModalProps {
  lead: LeadDetail["lead"];
  people: { id: number; name: string }[];
  onChanged: () => void;
  onClose: () => void;
}

type Change = "job_title" | "company" | "next_follow_up" | "summary" | "entry_summary";

interface ProposedChange {
  key: Change;
  label: string;
  current: string | null;
  proposed: string | null;
}

export default function CallNotesModal({ lead, people, onChanged, onClose }: CallNotesModalProps) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [channel, setChannel] = useState<InteractionChannel>("video");
  const [personId, setPersonId] = useState<number | null>(people[0]?.id ?? null);
  const [occurredAt, setOccurredAt] = useState(toLocalDateTimeInputValue());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CallNotesImportResponse | null>(null);
  const [selected, setSelected] = useState<Set<Change>>(new Set());

  const submit = async () => {
    if (!file && !text.trim()) {
      setError("Paste the notes or choose a file.");
      return;
    }
    const occurredUtc = localInputToUtcIso(occurredAt);
    setBusy(true);
    setError(null);
    try {
      let res: Response;
      if (file) {
        const form = new FormData();
        form.append("file", file);
        form.append("channel", channel);
        if (personId !== null) form.append("contact_id", String(personId));
        if (occurredUtc) form.append("occurred_at", occurredUtc);
        res = await fetch(`/api/leads/${lead.id}/call-notes`, { method: "POST", body: form });
      } else {
        res = await sendJson(`/api/leads/${lead.id}/call-notes`, "POST", {
          text,
          channel,
          contact_id: personId,
          occurred_at: occurredUtc,
        });
      }
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      const body = (await res.json()) as CallNotesImportResponse;
      setResult(body);
      setSelected(defaultSelection(body.proposal, lead));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (change: Change) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(change)) next.delete(change);
      else next.add(change);
      return next;
    });

  const apply = async (proposal: CallNotesProposal, interactionId: number) => {
    const leadFields: Record<string, string> = {};
    if (selected.has("job_title") && proposal.job_title) leadFields.job_title = proposal.job_title;
    if (selected.has("company") && proposal.hiring_company) leadFields.company = proposal.hiring_company;
    if (selected.has("next_follow_up") && proposal.next_follow_up) leadFields.next_follow_up = proposal.next_follow_up;
    if (selected.has("summary")) {
      leadFields.summary_html = appendedSummaryHtml(lead.summary_html, proposal, formatLocalDate(occurredAt.slice(0, 10)));
    }
    setBusy(true);
    setError(null);
    try {
      if (Object.keys(leadFields).length > 0) {
        const res = await sendJson(`/api/leads/${lead.id}`, "PATCH", leadFields);
        if (!res.ok) {
          setError(`Could not update the lead: ${await responseErrorText(res)}`);
          return;
        }
      }
      if (selected.has("entry_summary") && proposal.one_line_summary) {
        const res = await sendJson(`/api/interactions/${interactionId}`, "PATCH", { summary: proposal.one_line_summary });
        if (!res.ok) {
          setError(`Could not update the timeline entry: ${await responseErrorText(res)}`);
          return;
        }
      }
      onChanged();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    const { proposal } = result;
    const summaryItems = proposal ? proposalSummaryItems(proposal) : [];
    const candidates: ProposedChange[] = proposal
      ? [
          { key: "job_title", label: "Role", current: lead.job_title, proposed: proposal.job_title },
          { key: "company", label: "Hiring company", current: lead.company, proposed: proposal.hiring_company },
          {
            key: "next_follow_up",
            label: "Follow up on",
            current: lead.next_follow_up ? formatLocalDate(lead.next_follow_up) : null,
            proposed: proposal.next_follow_up ? formatLocalDate(proposal.next_follow_up) : null,
          },
          { key: "entry_summary", label: "Timeline entry title", current: result.interaction.summary, proposed: proposal.one_line_summary },
        ]
      : [];
    const options = candidates.filter((option) => option.proposed && option.proposed !== option.current);
    return (
      <Modal
        title="Review what the notes say"
        subtitle="The notes are saved on the timeline. Tick the changes to make to the lead."
        onClose={onClose}
        maxWidth="760px"
        footer={
          proposal ? (
            <>
              <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Skip</button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy || selected.size === 0}
                onClick={() => void apply(proposal, result.interaction.id)}
              >
                {busy ? "Applying…" : `Apply ${selected.size} change${selected.size === 1 ? "" : "s"}`}
              </button>
            </>
          ) : (
            <button type="button" className="btn-primary" onClick={onClose}>Done</button>
          )
        }
      >
        {!proposal && (
          <p role="alert">
            The notes were saved, but the AI couldn't read them: {result.ai_error || "no reply"}. You can still edit the
            lead by hand.
          </p>
        )}
        {proposal && options.length === 0 && summaryItems.length === 0 && (
          <p>Nothing new in these notes beyond what the lead already has.</p>
        )}
        <div className="form-stack">
          {options.map((option) => (
            <label key={option.key} style={{ flexDirection: "row", alignItems: "flex-start", gap: 8 }}>
              <input type="checkbox" checked={selected.has(option.key)} onChange={() => toggle(option.key)} />
              <span>
                <strong>{option.label}:</strong> {option.proposed}
                {option.current && <span className="lead-sub">now: {option.current}</span>}
              </span>
            </label>
          ))}
          {summaryItems.length > 0 && (
            <label style={{ flexDirection: "row", alignItems: "flex-start", gap: 8 }}>
              <input type="checkbox" checked={selected.has("summary")} onChange={() => toggle("summary")} />
              <span>
                <strong>Add to the lead's summary:</strong>
                <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                  {summaryItems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </span>
            </label>
          )}
          {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="Import call notes"
      subtitle="A transcript or an AI note-taker's summary (Gemini, Zoom, Teams, Otter, Fathom…)"
      onClose={onClose}
      maxWidth="760px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Reading the notes…" : "Import"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <label>
          Paste the notes
          <textarea
            rows={10}
            value={text}
            disabled={file !== null}
            placeholder="Paste the transcript or summary here, or choose a file below"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label>
          …or upload a file ({ACCEPTED_FILES.replace(/,/g, ", ")})
          <input type="file" accept={ACCEPTED_FILES} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div className="form-row">
          <label>
            How
            <select value={channel} onChange={(e) => setChannel(e.target.value as InteractionChannel)}>
              {(Object.keys(CHANNEL_LABELS) as InteractionChannel[]).map((c) => (
                <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>
              ))}
            </select>
          </label>
          {people.length > 0 && (
            <label>
              With
              <select value={personId ?? ""} onChange={(e) => setPersonId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">(no one in particular)</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
          )}
          <label>
            When
            <input type="datetime-local" value={occurredAt} onChange={(e) => setOccurredAt(e.target.value)} />
          </label>
        </div>
        <p className="lead-sub" style={{ margin: 0 }}>
          The notes are sent to your configured AI provider to suggest updates; nothing changes on the lead until you
          approve it.
        </p>
        {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
      </div>
    </Modal>
  );
}

/** Tick the changes that fill a gap; leave unticked the ones that would overwrite. */
function defaultSelection(proposal: CallNotesProposal | null, lead: LeadDetail["lead"]): Set<Change> {
  const selected = new Set<Change>();
  if (!proposal) return selected;
  if (proposal.job_title && !lead.job_title) selected.add("job_title");
  if (proposal.hiring_company && (!lead.company || lead.company === "Undisclosed")) selected.add("company");
  if (proposal.next_follow_up && !lead.next_follow_up) selected.add("next_follow_up");
  if (proposalSummaryItems(proposal).length > 0) selected.add("summary");
  if (proposal.one_line_summary) selected.add("entry_summary");
  return selected;
}
