/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// A contact's side of recruiter lead tracking, shown in their Contacts-tab detail: the
// leads they're attached to and every call, message and note logged with them
// (GET /api/contacts/<id>/activity), with Log and Create-lead actions.
import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import RichTextView from "../common/RichTextView.tsx";
import type { ApplicationStageStatus } from "../../types/applications.ts";
import type { Contact } from "../../types/contacts.ts";
import type { ContactActivity } from "../../types/leads.ts";
import { CHANNEL_LABELS, KIND_LABELS, ROLE_LABELS } from "../../types/leads.ts";
import { formatLocalDateTime } from "../../utils/dateTimeUtils.ts";
import { getJson, loadStageCatalog, stageLabel } from "./leadsShared.ts";

// Lazy: these carry the rich-text editor, which a contact's detail only needs once
// the user logs something or opens a lead.
const InteractionModal = lazy(() => import("./InteractionModal.tsx"));
const LeadDrawer = lazy(() => import("./LeadDrawer.tsx"));
const NewLeadModal = lazy(() => import("./NewLeadModal.tsx"));

interface ContactLeadsSectionProps {
  contact: Contact;
  /** Every contact, for the pickers in the new-lead form and lead drawer. */
  contacts: Contact[];
}

export default function ContactLeadsSection({ contact, contacts }: ContactLeadsSectionProps) {
  const [activity, setActivity] = useState<ContactActivity | null>(null);
  const [catalog, setCatalog] = useState<ApplicationStageStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [logging, setLogging] = useState(false);
  const [creatingLead, setCreatingLead] = useState(false);
  const [openLeadId, setOpenLeadId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setActivity(await getJson<ContactActivity>(`/api/contacts/${contact.id}/activity`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [contact.id]);

  useEffect(() => {
    let cancelled = false;
    void load();
    loadStageCatalog()
      .then((stages) => {
        if (!cancelled) setCatalog(stages);
      })
      .catch(() => {
        // Stage keys still show (humanized) without the catalog's labels.
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
          Leads &amp; conversations
        </strong>
        <span style={{ flex: 1 }} />
        <button type="button" className="btn-secondary btn-sm" onClick={() => setLogging(true)}>
          Log call / message / note
        </button>
        <button type="button" className="btn-secondary btn-sm" onClick={() => setCreatingLead(true)}>
          Create lead
        </button>
      </div>
      {error && <p role="alert" style={{ color: "var(--red)" }}>Could not load their history: {error}</p>}
      {activity && activity.jobs.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: "8px 0" }}>
          {activity.jobs.map((job) => (
            <li key={job.job_id} className="lead-person">
              <button type="button" className="btn-secondary btn-xs" onClick={() => setOpenLeadId(job.job_id)}>
                Open
              </button>
              <strong>{job.company || "—"}</strong>
              <span>{job.job_title || "Role not named yet"}</span>
              <span className="badge badge-gray">{stageLabel(catalog, job.stage)}</span>
              <span className="lead-sub">{ROLE_LABELS[job.role]}{job.is_primary ? " · primary" : ""}</span>
            </li>
          ))}
        </ul>
      )}
      {activity && activity.interactions.length === 0 && activity.jobs.length === 0 && (
        <p className="lead-sub">Nothing logged with them yet.</p>
      )}
      {activity && activity.interactions.length > 0 && (
        <ul className="lead-timeline" style={{ marginTop: 8 }}>
          {activity.interactions.map((interaction) => (
            <li key={interaction.id}>
              <div className="lead-timeline-meta">
                <span>{formatLocalDateTime(interaction.occurred_at)}</span>
                <strong>{KIND_LABELS[interaction.kind]} · {CHANNEL_LABELS[interaction.channel]}</strong>
                <span>{interaction.direction === "inbound" ? "from them" : "from you"}</span>
                {interaction.job_company && <span>re {interaction.job_company}{interaction.job_title ? ` — ${interaction.job_title}` : ""}</span>}
              </div>
              {interaction.summary && <div style={{ fontWeight: 600 }}>{interaction.summary}</div>}
              <RichTextView html={interaction.body_html} />
            </li>
          ))}
        </ul>
      )}

      <Suspense fallback={null}>
      {logging && (
        <InteractionModal
          contactId={contact.id}
          onClose={() => setLogging(false)}
          onSaved={() => {
            setLogging(false);
            void load();
          }}
        />
      )}
      {creatingLead && (
        <NewLeadModal
          contacts={contacts}
          catalog={catalog}
          presetContactId={contact.id}
          onClose={() => setCreatingLead(false)}
          onCreated={(jobId) => {
            setCreatingLead(false);
            void load();
            setOpenLeadId(jobId);
          }}
          onOpenExisting={(jobId) => {
            setCreatingLead(false);
            setOpenLeadId(jobId);
          }}
        />
      )}
      {openLeadId !== null && (
        <LeadDrawer
          jobId={openLeadId}
          catalog={catalog}
          contacts={contacts}
          onChanged={() => void load()}
          onClose={() => setOpenLeadId(null)}
        />
      )}
      </Suspense>
    </div>
  );
}
