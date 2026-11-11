/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Log a call, message, note or transcript -- or edit one already logged -- against a
// lead, a contact, or both (POST /api/interactions, PATCH /api/interactions/<id>).
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import RichTextEditor from "../common/RichTextEditor.tsx";
import { isRichTextEmpty } from "../../utils/richText.ts";
import type {
  Interaction,
  InteractionChannel,
  InteractionDirection,
  InteractionKind,
} from "../../types/leads.ts";
import { CHANNEL_LABELS, KIND_LABELS } from "../../types/leads.ts";
import { responseErrorText, sendJson } from "../../utils/contactsUtils.ts";
import { localInputToUtcIso, parseUtcTimestamp, toLocalDateTimeInputValue } from "../../utils/dateTimeUtils.ts";

interface PersonOption {
  id: number;
  name: string;
}

interface InteractionModalProps {
  /** The lead this is about, if any. */
  jobId?: number | null;
  /** People the user can say it was with (a lead's people); omit to fix it to contactId. */
  people?: PersonOption[];
  /** The contact it is with, preselected (or fixed when no `people` are given). */
  contactId?: number | null;
  /** An interaction to edit instead of logging a new one. */
  existing?: Interaction | null;
  onSaved: () => void;
  onClose: () => void;
}

const CHANNELS = Object.keys(CHANNEL_LABELS) as InteractionChannel[];
const KINDS = Object.keys(KIND_LABELS) as InteractionKind[];

export default function InteractionModal({
  jobId = null,
  people,
  contactId = null,
  existing = null,
  onSaved,
  onClose,
}: InteractionModalProps) {
  const [channel, setChannel] = useState<InteractionChannel>(existing?.channel || "phone");
  const [kind, setKind] = useState<InteractionKind>(existing?.kind || "call");
  const [direction, setDirection] = useState<InteractionDirection>(existing?.direction || "inbound");
  const [occurredAt, setOccurredAt] = useState(
    toLocalDateTimeInputValue(parseUtcTimestamp(existing?.occurred_at) || new Date()),
  );
  const [summary, setSummary] = useState(existing?.summary || "");
  const [bodyHtml, setBodyHtml] = useState(existing?.body_html || "");
  const [personId, setPersonId] = useState<number | null>(existing?.contact_id ?? contactId ?? people?.[0]?.id ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const occurredUtc = localInputToUtcIso(occurredAt);
    if (!occurredUtc) {
      setError("Enter when it happened.");
      return;
    }
    if (!existing && jobId === null && personId === null) {
      setError("Choose who it was with.");
      return;
    }
    const fields = {
      channel,
      kind,
      direction,
      occurred_at: occurredUtc,
      summary: summary.trim() || null,
      body_html: isRichTextEmpty(bodyHtml) ? null : bodyHtml,
    };
    setSaving(true);
    setError(null);
    try {
      const res = existing
        ? await sendJson(`/api/interactions/${existing.id}`, "PATCH", fields)
        : await sendJson("/api/interactions", "POST", { ...fields, job_id: jobId, contact_id: personId });
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={existing ? "Edit entry" : "Log a call, message or note"}
      onClose={onClose}
      maxWidth="720px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <div className="form-row">
          <label>
            What
            <select value={kind} onChange={(event) => setKind(event.target.value as InteractionKind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>{KIND_LABELS[k]}</option>
              ))}
            </select>
          </label>
          <label>
            How
            <select value={channel} onChange={(event) => setChannel(event.target.value as InteractionChannel)}>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>
              ))}
            </select>
          </label>
          <label>
            Who started it
            <select value={direction} onChange={(event) => setDirection(event.target.value as InteractionDirection)}>
              <option value="inbound">They did</option>
              <option value="outbound">I did</option>
            </select>
          </label>
          <label>
            When
            <input type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} />
          </label>
        </div>
        {!existing && people && people.length > 0 && (
          <label>
            With
            <select
              value={personId ?? ""}
              onChange={(event) => setPersonId(event.target.value ? Number(event.target.value) : null)}
            >
              {jobId !== null && <option value="">(no one in particular)</option>}
              {people.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          One-line summary
          <input
            type="text"
            value={summary}
            maxLength={1000}
            placeholder="e.g. Recruiter screen — rate 90/hr, client is a fintech"
            onChange={(event) => setSummary(event.target.value)}
          />
        </label>
        <span className="lead-sub" style={{ fontWeight: 600 }}>Notes</span>
        <RichTextEditor
          initialHtml={existing?.body_html}
          onChange={setBodyHtml}
          ariaLabel="Notes"
          placeholder="What was said, next steps, anything worth remembering"
        />
        {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
      </div>
    </Modal>
  );
}
