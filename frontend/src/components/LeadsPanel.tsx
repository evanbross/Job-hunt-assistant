/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Leads tab (recruiter lead tracking, phase 1): which leads are still open, where each
// stands, when anything last happened, and conversations not tied to a role yet.
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import type { ApplicationStageStatus } from "../types/applications.ts";
import type { Contact, ContactsListResponse } from "../types/contacts.ts";
import type { LeadSummary, LeadsBoard } from "../types/leads.ts";
import { CHANNEL_LABELS, ORIGIN_LABELS } from "../types/leads.ts";
import { formatLocalDate } from "../utils/dateTimeUtils.ts";
import { getJson, loadStageCatalog, localToday, needsAttention, stageLabel } from "./leads/leadsShared.ts";
import SuggestedLeads from "./leads/SuggestedLeads.tsx";

// Lazy: these carry the rich-text editor, which the board itself doesn't need.
const InteractionModal = lazy(() => import("./leads/InteractionModal.tsx"));
const LeadDrawer = lazy(() => import("./leads/LeadDrawer.tsx"));
const NewLeadModal = lazy(() => import("./leads/NewLeadModal.tsx"));

type Scope = "active" | "attention" | "closed" | "all";

const SCOPE_LABELS: Record<Scope, string> = {
  active: "Open",
  attention: "Needs attention",
  closed: "Closed",
  all: "All",
};

function inScope(lead: LeadSummary, scope: Scope, today: string): boolean {
  if (scope === "all") return true;
  if (scope === "closed") return lead.is_closed;
  if (scope === "attention") return needsAttention(lead, today);
  return !lead.is_closed;
}

export default function LeadsPanel() {
  const [board, setBoard] = useState<LeadsBoard | null>(null);
  const [catalog, setCatalog] = useState<ApplicationStageStatus[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("active");
  const [search, setSearch] = useState("");
  const [openLeadId, setOpenLeadId] = useState<number | null>(null);
  const [newLead, setNewLead] = useState<{ contactId: number | null } | null>(null);
  const [logForContact, setLogForContact] = useState<number | null>(null);

  const loadBoard = useCallback(async () => {
    try {
      setBoard(await getJson<LeadsBoard>("/api/leads"));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadContacts = useCallback(async () => {
    try {
      setContacts((await getJson<ContactsListResponse>("/api/contacts")).contacts);
    } catch {
      // The board still works without the contact picker's list; picking shows none.
      setContacts([]);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([loadBoard(), loadContacts(), loadStageCatalog()]).then(([, , stages]) => {
      if (!cancelled) setCatalog(stages);
    }).catch((e: unknown) => {
      if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
    });
    return () => {
      cancelled = true;
    };
  }, [loadBoard, loadContacts]);

  const today = localToday();
  const visibleLeads = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (board?.leads || []).filter(
      (lead) =>
        inScope(lead, scope, today) &&
        (!needle ||
          `${lead.company || ""} ${lead.job_title || ""} ${lead.primary_contact_name || ""} ${lead.primary_contact_company || ""}`
            .toLowerCase()
            .includes(needle)),
    );
  }, [board, scope, search, today]);

  const counts = useMemo(() => {
    const leads = board?.leads || [];
    return {
      active: leads.filter((l) => !l.is_closed).length,
      attention: leads.filter((l) => needsAttention(l, today)).length,
      closed: leads.filter((l) => l.is_closed).length,
      all: leads.length,
    } satisfies Record<Scope, number>;
  }, [board, today]);

  const refreshAll = () => {
    void loadBoard();
    void loadContacts();
  };

  return (
    <div>
      <div className="panel-toolbar">
        <div className="panel-toolbar-title">Leads</div>
        <div className="panel-toolbar-actions">
          <button type="button" className="btn-primary" onClick={() => setNewLead({ contactId: null })}>
            New recruiter lead
          </button>
        </div>
      </div>

      {loadError && <p role="alert" style={{ color: "var(--red)" }}>Could not load leads: {loadError}</p>}

      <div className="leads-filters" role="group" aria-label="Which leads">
        {(Object.keys(SCOPE_LABELS) as Scope[]).map((key) => (
          <button
            key={key}
            type="button"
            className={scope === key ? "btn-primary" : "btn-secondary"}
            aria-pressed={scope === key}
            onClick={() => setScope(key)}
          >
            {SCOPE_LABELS[key]} ({counts[key]})
          </button>
        ))}
        <input
          type="search"
          value={search}
          placeholder="Search company, role or recruiter"
          aria-label="Search leads"
          onChange={(e) => setSearch(e.target.value)}
          style={{ marginLeft: "auto", minWidth: 240 }}
        />
      </div>

      {board && visibleLeads.length === 0 && (
        <p className="lead-sub">
          {board.leads.length === 0
            ? "No leads yet. When a recruiter calls or messages you about a role, add it with “New recruiter lead”."
            : "No leads match."}
        </p>
      )}
      {visibleLeads.length > 0 && (
        <table className="leads-table">
          <thead>
            <tr>
              <th>Company / role</th>
              <th>Recruiter</th>
              <th>Stage</th>
              <th>Last activity</th>
              <th>Follow up</th>
            </tr>
          </thead>
          <tbody>
            {visibleLeads.map((lead) => (
              <tr
                key={lead.id}
                className={`lead-row${lead.is_stale ? " stale" : ""}${lead.is_closed ? " closed" : ""}`}
                onClick={() => setOpenLeadId(lead.id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setOpenLeadId(lead.id);
                }}
              >
                <td>
                  <strong>{lead.company || "—"}</strong>
                  <span className="lead-sub">
                    {lead.job_title || "Role not named yet"}
                    {lead.origin !== "recruiter_contact" ? ` · ${ORIGIN_LABELS[lead.origin]}` : ""}
                  </span>
                </td>
                <td>
                  {lead.primary_contact_name || "—"}
                  {lead.primary_contact_company && <span className="lead-sub">{lead.primary_contact_company}</span>}
                  {lead.contact_count > 1 && <span className="lead-sub">+{lead.contact_count - 1} more</span>}
                </td>
                <td>{stageLabel(catalog, lead.stage)}</td>
                <td>
                  {lead.last_activity_date ? formatLocalDate(lead.last_activity_date) : "—"}
                  {lead.idle_days !== null && (
                    <span className="lead-sub">
                      {lead.idle_days === 0 ? "today" : `${lead.idle_days} day${lead.idle_days === 1 ? "" : "s"} ago`}
                      {lead.is_stale ? " · gone quiet" : ""}
                    </span>
                  )}
                </td>
                <td>
                  {lead.next_follow_up ? formatLocalDate(lead.next_follow_up) : "—"}
                  {!lead.is_closed && lead.next_follow_up !== null && lead.next_follow_up <= today && (
                    <span className="lead-sub" style={{ color: "var(--red)" }}>due</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {board && (
        <p className="lead-sub" style={{ marginTop: 8 }}>
          Leads with nothing logged for more than {board.stale_after_days} days are highlighted as gone quiet
          (change this in Settings).
        </p>
      )}

      {/* ai: claude-opus-5-5 | 2026-11-11 -- phase 2: LinkedIn messages import */}
      <SuggestedLeads
        leads={board?.leads || []}
        onAccepted={(result) => {
          refreshAll();
          if (result.job_id !== null) setOpenLeadId(result.job_id);
        }}
      />

      <h3 className="leads-section-title">Conversations without a role yet</h3>
      {board && board.open_conversations.length === 0 && (
        <p className="lead-sub">
          None in the last {board.open_conversation_window_days} days. A call, reply or note with a contact that isn't
          tied to a lead shows up here.
        </p>
      )}
      {board && board.open_conversations.length > 0 && (
        <table className="leads-table">
          <thead>
            <tr>
              <th>Who</th>
              <th>Last contact</th>
              <th>Summary</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {board.open_conversations.map((conversation) => (
              <tr key={conversation.contact_id}>
                <td>
                  <strong>{conversation.full_name}</strong>
                  <span className="lead-sub">
                    {[conversation.position, conversation.company_raw].filter(Boolean).join(" · ")}
                  </span>
                </td>
                <td>
                  {formatLocalDate(conversation.last_interaction_at)}
                  <span className="lead-sub">{CHANNEL_LABELS[conversation.last_channel]}</span>
                </td>
                <td>{conversation.last_summary || "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button type="button" className="btn-secondary" onClick={() => setLogForContact(conversation.contact_id)}>
                    Log
                  </button>{" "}
                  <button type="button" className="btn-secondary" onClick={() => setNewLead({ contactId: conversation.contact_id })}>
                    Create lead
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Suspense fallback={null}>
      {openLeadId !== null && (
        <LeadDrawer
          jobId={openLeadId}
          catalog={catalog}
          contacts={contacts}
          onChanged={refreshAll}
          onClose={() => setOpenLeadId(null)}
        />
      )}
      {newLead && (
        <NewLeadModal
          contacts={contacts}
          catalog={catalog}
          presetContactId={newLead.contactId}
          onClose={() => setNewLead(null)}
          onCreated={(jobId) => {
            setNewLead(null);
            refreshAll();
            setOpenLeadId(jobId);
          }}
          onOpenExisting={(jobId) => {
            setNewLead(null);
            setOpenLeadId(jobId);
          }}
        />
      )}
      {logForContact !== null && (
        <InteractionModal
          contactId={logForContact}
          onClose={() => setLogForContact(null)}
          onSaved={() => {
            setLogForContact(null);
            void loadBoard();
          }}
        />
      )}
      </Suspense>
    </div>
  );
}
