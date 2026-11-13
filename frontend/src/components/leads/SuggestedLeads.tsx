/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// The Leads tab's "Suggested from LinkedIn" inbox (recruiter lead tracking, phase 2):
// conversations from an imported messages.csv that look like a recruiter reaching
// out, for the user to accept or dismiss. Nothing becomes a contact or lead until
// accepted. Re-reads on the "lead-suggestions" server event, which the AI check
// publishes after every batch, so suggestions appear while it runs.
import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { useServerEvent } from "../../hooks/useServerEvents.ts";
import type { AcceptSuggestionResponse, LeadSuggestion, LeadSuggestionsResponse, LeadSummary, SuggestionTab } from "../../types/leads.ts";
import { SUGGESTION_TAB_LABELS } from "../../types/leads.ts";
import { responseErrorText, sendJson } from "../../utils/contactsUtils.ts";
import { formatLocalDate } from "../../utils/dateTimeUtils.ts";
import { getJson } from "./leadsShared.ts";

const AcceptSuggestionModal = lazy(() => import("./AcceptSuggestionModal.tsx"));
const ImportMessagesModal = lazy(() => import("./ImportMessagesModal.tsx"));

const DETECTION_LABELS: Record<NonNullable<LeadSuggestion["detection"]>, string> = {
  contact: "Contacts",
  title: "Their title",
  ai: "AI",
};

interface SuggestedLeadsProps {
  leads: LeadSummary[];
  onAccepted: (result: AcceptSuggestionResponse) => void;
}

export default function SuggestedLeads({ leads, onAccepted }: SuggestedLeadsProps) {
  const [tab, setTab] = useState<SuggestionTab>("suggested");
  const [data, setData] = useState<LeadSuggestionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [importing, setImporting] = useState(false);
  const [accepting, setAccepting] = useState<LeadSuggestion | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getJson<LeadSuggestionsResponse>(`/api/lead-suggestions?status=${tab}`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [tab]);

  useEffect(() => {
    void load();
  }, [load]);
  useServerEvent("lead-suggestions", load);

  const post = async (url: string, id: number | null) => {
    setBusyId(id);
    setError(null);
    try {
      const res = await sendJson(url, "POST", {});
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const counts = data?.counts;
  const check = data?.check;
  const nothingImported = counts !== undefined && Object.values(counts).every((n) => n === 0);

  return (
    <section aria-label="Suggested from LinkedIn">
      <div className="panel-toolbar">
        <h3 className="leads-section-title" style={{ margin: 0 }}>Suggested from LinkedIn</h3>
        <div className="panel-toolbar-actions">
          {counts && counts.unclassified > 0 && !check?.running && (
            <button type="button" className="btn-secondary" disabled={busyId !== null} onClick={() => void post("/api/lead-suggestions/check", null)}>
              Check {counts.unclassified} with AI
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={() => setImporting(true)}>
            Import LinkedIn messages
          </button>
        </div>
      </div>
      {check?.running && (
        <p className="lead-sub" role="status">Checking conversations with AI… {check.checked} done so far.</p>
      )}
      {check && !check.running && check.error && (
        <p role="alert" style={{ color: "var(--red)" }}>The AI check stopped: {check.error}</p>
      )}
      {error && <p role="alert" style={{ color: "var(--red)" }}>{error}</p>}

      {nothingImported && (
        <p className="lead-sub">
          Import your LinkedIn messages to find recruiters who reached out. Nothing is added to Contacts or Leads until you
          accept it.
        </p>
      )}
      {data && counts && !nothingImported && (
        <>
          <div className="leads-filters" role="group" aria-label="Which suggestions">
            {(Object.keys(SUGGESTION_TAB_LABELS) as SuggestionTab[]).map((key) => (
              <button
                key={key}
                type="button"
                className={tab === key ? "btn-primary" : "btn-secondary"}
                aria-pressed={tab === key}
                onClick={() => setTab(key)}
              >
                {SUGGESTION_TAB_LABELS[key]} ({counts[key]})
              </button>
            ))}
          </div>
          {data.suggestions.length === 0 && <p className="lead-sub">Nothing here.</p>}
          {data.suggestions.length > 0 && (
            <table className="leads-table">
              <thead>
                <tr>
                  <th>Who</th>
                  <th>Last message</th>
                  <th>Why</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.suggestions.map((suggestion) => (
                  <SuggestionRow
                    key={suggestion.id}
                    suggestion={suggestion}
                    tab={tab}
                    expanded={expanded.has(suggestion.id)}
                    busy={busyId === suggestion.id}
                    onToggle={() => toggle(suggestion.id)}
                    onAccept={() => setAccepting(suggestion)}
                    onMove={(action) => void post(`/api/lead-suggestions/${suggestion.id}/${action}`, suggestion.id)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <Suspense fallback={null}>
        {importing && <ImportMessagesModal onImported={() => void load()} onClose={() => setImporting(false)} />}
        {accepting && (
          <AcceptSuggestionModal
            suggestion={accepting}
            leads={leads}
            onClose={() => setAccepting(null)}
            onAccepted={(result) => {
              setAccepting(null);
              void load();
              onAccepted(result);
            }}
          />
        )}
      </Suspense>
    </section>
  );
}

interface SuggestionRowProps {
  suggestion: LeadSuggestion;
  tab: SuggestionTab;
  expanded: boolean;
  busy: boolean;
  onToggle: () => void;
  onAccept: () => void;
  onMove: (action: "dismiss" | "restore") => void;
}

function SuggestionRow({ suggestion, tab, expanded, busy, onToggle, onAccept, onMove }: SuggestionRowProps) {
  const opening = suggestion.messages.find((m) => !m.from_owner) ?? suggestion.messages[0];
  const guesses = [suggestion.role_guess, suggestion.hiring_company_guess && `at ${suggestion.hiring_company_guess}`]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <tr>
        <td>
          <strong>
            {suggestion.counterpart_profile_url ? (
              <a href={suggestion.counterpart_profile_url} target="_blank" rel="noopener noreferrer">
                {suggestion.counterpart_name}
              </a>
            ) : (
              suggestion.counterpart_name
            )}
          </strong>
          {suggestion.recruiter_company_guess && <span className="lead-sub">{suggestion.recruiter_company_guess}</span>}
          {suggestion.contact_id !== null && <span className="lead-sub">In your contacts</span>}
        </td>
        <td>
          {formatLocalDate(suggestion.last_message_at)}
          <button type="button" className="link-button lead-sub" aria-expanded={expanded} onClick={onToggle}>
            {expanded ? "Hide" : "Show"} {suggestion.message_count} message{suggestion.message_count === 1 ? "" : "s"}
          </button>
        </td>
        <td>
          {guesses && <span>{guesses}</span>}
          <span className="lead-sub">
            {suggestion.detection ? `${DETECTION_LABELS[suggestion.detection]}: ` : ""}
            {suggestion.reason || (opening ? opening.text.slice(0, 140) : "")}
          </span>
        </td>
        <td style={{ whiteSpace: "nowrap" }}>
          {tab !== "dismissed" && (
            <button type="button" className="btn-primary" disabled={busy} onClick={onAccept}>Accept…</button>
          )}{" "}
          {(tab === "dismissed" || tab === "not_recruiter") && (
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => onMove("restore")}>
              Restore
            </button>
          )}{" "}
          {tab !== "dismissed" && (
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => onMove("dismiss")}>
              Dismiss
            </button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={4}>
            <ol className="suggestion-messages">
              {suggestion.messages.map((message) => (
                <li key={message.external_ref}>
                  <span className="lead-sub">
                    {message.from_owner ? "You" : message.sender_name} · {formatLocalDate(message.sent_at)}
                    {message.subject ? ` · ${message.subject}` : ""}
                  </span>
                  <span className="suggestion-message-text">{message.text}</span>
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}
