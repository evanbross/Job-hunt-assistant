/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-14
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  MAX_OUTREACH_MESSAGE_LENGTH,
  rememberTemplateId,
  rememberedTemplateId,
  responseErrorText,
  safeHttpUrl,
  sendJson,
} from "../utils/contactsUtils.ts";
import { formatLocalDate } from "../utils/dateTimeUtils.ts";
import type {
  Contact,
  OutreachDraftResponse,
  OutreachTemplate,
  OutreachTemplateListResponse,
} from "../types/contacts.ts";

interface OutreachQueueProps {
  contacts: Contact[];
  // ai: claude-opus-5-5 | 2026-10-21
  /**
   * Contacts picked from the Contacts table's "Message" buttons, in table
   * order, or null for the default "Want to contact: Yes, nothing sent yet"
   * queue. Picked contacts are messaged regardless of those criteria (e.g. a
   * follow-up to someone already contacted).
   */
  targetIds: number[] | null;
  /** Leave the picked-contacts list and go back to the default queue. */
  onClearTarget: () => void;
  /** Reload the contact list after a send is logged. */
  onOutreachLogged: () => Promise<void>;
  // ai: claude-opus-5-5 | 2026-10-26
  /** Switch to the Templates tab (templates are edited there, not in the queue). */
  onManageTemplates: () => void;
}

// ai: claude-opus-5-5 | 2026-10-14
// Contacts marked "Want to contact: Yes" with nothing logged yet, highest
// Priority first -- the same outreach_score the contacts table sorts by.
function queueOrder(a: Contact, b: Contact): number {
  return (b.outreach_score ?? 0) - (a.outreach_score ?? 0) || a.full_name.localeCompare(b.full_name) || a.id - b.id;
}

// ai: claude-opus-5-5 | 2026-10-14
/**
 * Walks the "want to contact" list one person at a time: prepares a LinkedIn
 * message from the chosen template (optionally rewritten by AI), stages it so
 * the browser extension pastes it into that contact's LinkedIn message box,
 * then logs it as sent once the user confirms they pressed Send on LinkedIn.
 */
export default function OutreachQueue({
  contacts,
  targetIds,
  onClearTarget,
  onOutreachLogged,
  onManageTemplates,
}: OutreachQueueProps) {
  // ai: claude-opus-5-5 | 2026-10-21 -- named templates replace the single saved one
  const [templates, setTemplates] = useState<OutreachTemplate[] | null>(null); // null until loaded
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [skippedIds, setSkippedIds] = useState<Set<number>>(() => new Set());
  const [draft, setDraft] = useState("");
  const [draftEdited, setDraftEdited] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [openedForId, setOpenedForId] = useState<number | null>(null);
  // ai: claude-opus-5-5 | 2026-10-21
  // Logged as sent this session. The default queue also drops them via
  // outreach.length, but a picked contact may already have outreach (a
  // follow-up), so only this set can advance past them.
  const [sentIds, setSentIds] = useState<Set<number>>(() => new Set());

  // ai: claude-opus-5-5 | 2026-10-21 -- picked contacts keep the table order
  // the user saw when picking them; the default queue sorts by priority.
  const pending = useMemo(() => {
    if (targetIds === null) return contacts.filter((c) => c.interested === true && c.outreach.length === 0);
    const byId = new Map(contacts.map((c) => [c.id, c]));
    return targetIds.map((id) => byId.get(id)).filter((c): c is Contact => c !== undefined);
  }, [contacts, targetIds]);
  const queue = useMemo(() => {
    const ready = pending.filter((c) => safeHttpUrl(c.profile_url) && !skippedIds.has(c.id) && !sentIds.has(c.id));
    return targetIds === null ? ready.sort(queueOrder) : ready;
  }, [pending, skippedIds, sentIds, targetIds]);
  const missingProfileCount = pending.filter((c) => !safeHttpUrl(c.profile_url)).length;
  // ai: claude-opus-5-5 | 2026-10-26 -- the contact picked from the "Up next"
  // list, if any; falls back to the head of the queue once they leave it.
  const [jumpedToId, setJumpedToId] = useState<number | null>(null);
  const current = queue.find((c) => c.id === jumpedToId) ?? queue[0] ?? null;
  const upNext = queue.filter((c) => c !== current);
  const currentId = current?.id ?? null;
  const profileUrl = safeHttpUrl(current?.profile_url);

  const templatesLoaded = templates !== null;

  // ai: claude-opus-5-5 | 2026-10-21
  useEffect(() => {
    let cancelled = false;
    fetch("/api/outreach-templates")
      .then(async (res) => {
        if (!res.ok) throw new Error(await responseErrorText(res));
        return res.json() as Promise<OutreachTemplateListResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        setTemplates(data.templates);
        setTemplateId(rememberedTemplateId(data.templates));
      })
      .catch((e) => {
        if (!cancelled) setStatus(`Could not load your message templates: ${e instanceof Error ? e.message : String(e)}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ai: claude-opus-5-5 | 2026-10-21 -- takes the template to draft from
  const requestDraft = async (contactId: number, fromTemplateId: number, useAi: boolean): Promise<string> => {
    const res = await sendJson(`/api/contacts/${contactId}/outreach-draft/${fromTemplateId}`, "POST", { use_ai: useAi });
    if (!res.ok) throw new Error(await responseErrorText(res));
    const data: OutreachDraftResponse = await res.json();
    return data.draft;
  };

  // A new contact at the head of the queue: resume the draft staged for them
  // earlier, if any, otherwise start from the template. Read-only request, so
  // a StrictMode double-run is harmless; `cancelled` drops a stale response
  // if the queue moved on before it arrived.
  // ai: claude-opus-5-5 | 2026-10-21 -- also reruns once the templates load,
  // and leaves the draft empty when there is no template to start from.
  useEffect(() => {
    if (currentId === null || !templatesLoaded) return undefined;
    const savedDraft = contacts.find((c) => c.id === currentId)?.outreach_draft;
    setOpenedForId(null);
    setDraftEdited(Boolean(savedDraft));
    if (savedDraft) {
      setDraft(savedDraft);
      return undefined;
    }
    setDraft("");
    if (templateId === null) return undefined;
    let cancelled = false;
    setDraftLoading(true);
    requestDraft(currentId, templateId, false)
      .then((text) => {
        if (!cancelled) setDraft(text);
      })
      .catch((e) => {
        if (!cancelled) setStatus(`Could not build a draft: ${e instanceof Error ? e.message : String(e)}`);
      })
      .finally(() => {
        if (!cancelled) setDraftLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // contacts is deliberately omitted: a reload after an unrelated edit must
    // not throw away the draft being worked on for the same person. templateId
    // too: picking a template redrafts through chooseTemplate(), which asks
    // before replacing an edited draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, templatesLoaded]);

  // ai: claude-opus-5-5 | 2026-10-21 -- takes the template explicitly, so a
  // caller that just changed it doesn't read a stale templateId.
  const loadDraft = async (fromTemplateId: number, useAi: boolean) => {
    if (currentId === null) return;
    setDraftLoading(true);
    setStatus(useAi ? "Asking the AI for a personalized draft…" : "");
    try {
      const text = await requestDraft(currentId, fromTemplateId, useAi);
      setDraft(text);
      setDraftEdited(useAi);
      setStatus("");
    } catch (e) {
      setStatus(`Could not build a draft: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDraftLoading(false);
    }
  };

  // ai: claude-opus-5-5 | 2026-10-21
  const regenerate = async (useAi: boolean) => {
    if (templateId === null) return;
    if (draftEdited && !window.confirm("Replace your edited draft?")) return;
    await loadDraft(templateId, useAi);
  };

  // ai: claude-opus-5-5 | 2026-10-26 -- selectTemplate() folded in; it had no other caller once template editing moved to its own tab
  const chooseTemplate = async (nextId: number) => {
    if (draftEdited && !window.confirm("Replace your edited draft with this template?")) return;
    setTemplateId(nextId);
    rememberTemplateId(nextId);
    await loadDraft(nextId, false);
  };

  const openInLinkedIn = async () => {
    if (!current || !profileUrl) return;
    const message = draft.trim();
    if (!message) {
      window.alert("Write a message first.");
      return;
    }
    setBusy(true);
    try {
      const res = await sendJson(`/api/contacts/${current.id}/outreach-draft/stage`, "POST", { draft: message });
      if (!res.ok) throw new Error(await responseErrorText(res));
      // Clipboard is the fallback if the extension can't reach the message
      // box (not a 1st-degree connection, LinkedIn layout change, extension off).
      let copied = true;
      try {
        await navigator.clipboard.writeText(message);
      } catch {
        copied = false;
      }
      window.open(profileUrl, "_blank", "noopener,noreferrer");
      setOpenedForId(current.id);
      setStatus(
        `Opened ${current.full_name}'s profile. The extension will open the message box and paste the draft` +
          (copied ? " (it's on your clipboard too)." : ".") +
          " Press Send on LinkedIn, then come back and click \"I sent it\".",
      );
    } catch (e) {
      window.alert(`Could not prepare the message: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const markSent = async () => {
    if (!current) return;
    setBusy(true);
    try {
      const res = await sendJson(`/api/contacts/${current.id}/outreach`, "POST", { channel: "linkedin" });
      if (!res.ok) throw new Error(await responseErrorText(res));
      setSentIds((prev) => new Set(prev).add(current.id));
      setJumpedToId(null); // ai: claude-opus-5-5 | 2026-10-26 -- back to queue order
      setStatus(`Logged your message to ${current.full_name}.`);
      await onOutreachLogged();
    } catch (e) {
      window.alert(`Could not log the message: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const skip = () => {
    if (!current) return;
    setSkippedIds((prev) => new Set(prev).add(current.id));
    setJumpedToId(null); // ai: claude-opus-5-5 | 2026-10-26 -- back to queue order
    setStatus(`Skipped ${current.full_name} for now.`);
  };

  // ai: claude-opus-5-5 | 2026-10-26
  // An unstaged draft only lives in this component, so moving to someone else
  // would lose it; ask first, like the template picker does.
  const jumpTo = (next: Contact) => {
    if (draftEdited && current && !window.confirm(`Discard your edited draft to ${current.full_name}?`)) return;
    setJumpedToId(next.id);
    setStatus("");
  };

  return (
    <div className="card" style={{ marginBottom: "16px" }}>
      <div style={headerRow}>
        <div style={{ flex: 1 }}>
          {/* ai: claude-opus-5-5 | 2026-10-21 -- picked-contacts wording */}
          <div style={{ fontWeight: 700 }}>
            {targetIds === null
              ? "LinkedIn outreach queue"
              : `Messaging ${targetIds.length.toLocaleString()} selected contact${targetIds.length === 1 ? "" : "s"}`}
          </div>
          <div style={mutedText}>
            {queue.length === 0
              ? "Nobody left to message."
              : `${queue.length.toLocaleString()} to go, ${targetIds === null ? "highest priority" : "in table order"} first.`}
            {skippedIds.size > 0 && ` ${skippedIds.size} skipped this session.`}
            {missingProfileCount > 0 &&
              ` ${missingProfileCount} ${targetIds === null ? 'marked "Yes"' : "selected"} have no LinkedIn profile URL and aren't listed.`}
          </div>
        </div>
        {skippedIds.size > 0 && (
          <button className="btn-secondary btn-sm" disabled={busy} onClick={() => setSkippedIds(new Set())}>
            Bring back skipped
          </button>
        )}
        {targetIds !== null && (
          <button className="btn-secondary btn-sm" disabled={busy} onClick={onClearTarget}>
            Back to full queue
          </button>
        )}
      </div>

      {current && (
        <div style={{ marginTop: "14px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "16px", fontWeight: 700 }}>{current.full_name}</span>
            <span style={mutedText}>
              {[current.position, current.company_raw].filter(Boolean).join(" at ") || "No title or company"}
            </span>
            <span style={mutedText} title="Weighted outreach priority">
              Priority {current.outreach_score?.toFixed(1) ?? "—"}
            </span>
          </div>
          {current.notes && <div style={{ ...mutedText, fontStyle: "italic", marginTop: "2px" }}>{current.notes}</div>}
          {/* ai: claude-opus-5-5 | 2026-10-21 -- only reachable via a picked contact */}
          {current.outreach.length > 0 && (
            <div style={{ ...mutedText, marginTop: "2px" }}>
              Already contacted {current.outreach.length}x, last on{" "}
              {formatLocalDate(current.outreach[current.outreach.length - 1].sent_at)}.
            </div>
          )}

          {/* ai: claude-opus-5-5 | 2026-10-21 */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "10px" }}>
            <label style={mutedText} htmlFor="outreach-template-choice">
              Template
            </label>
            {templates !== null && templates.length > 0 ? (
              <select
                id="outreach-template-choice"
                value={templateId ?? ""}
                disabled={busy || draftLoading}
                onChange={(e) => chooseTemplate(Number(e.target.value))}
              >
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
            ) : (
              <span style={mutedText}>
                {/* ai: claude-opus-5-5 | 2026-10-26 */}
                {templates === null ? (
                  "Loading…"
                ) : (
                  <>
                    None yet.{" "}
                    <button className="btn-secondary btn-xs" onClick={onManageTemplates}>
                      Create a template
                    </button>
                  </>
                )}
              </span>
            )}
          </div>

          <textarea
            value={draft}
            rows={7}
            maxLength={MAX_OUTREACH_MESSAGE_LENGTH}
            disabled={draftLoading}
            placeholder={draftLoading ? "Preparing a draft…" : "Your message"}
            onChange={(e) => {
              setDraft(e.target.value);
              setDraftEdited(true);
            }}
            aria-label={`Message to ${current.full_name}`}
            style={{ width: "100%", resize: "vertical", marginTop: "6px" }}
          />
          <div style={{ ...mutedText, textAlign: "right" }}>
            {draft.length.toLocaleString()} / {MAX_OUTREACH_MESSAGE_LENGTH.toLocaleString()}
          </div>

          <div style={actionRow}>
            <button
              className="btn-secondary btn-sm"
              disabled={busy || draftLoading || templateId === null}
              onClick={() => regenerate(true)}
            >
              Draft with AI
            </button>
            <button
              className="btn-secondary btn-sm"
              disabled={busy || draftLoading || templateId === null}
              onClick={() => regenerate(false)}
            >
              Reset to template
            </button>
            <span style={{ flex: 1 }} />
            <button className="btn-secondary btn-sm" disabled={busy} onClick={skip}>
              Skip for now
            </button>
            <button
              className={openedForId === current.id ? "btn-secondary btn-sm" : "btn-primary btn-sm"}
              disabled={busy || draftLoading || !draft.trim()}
              onClick={openInLinkedIn}
            >
              {openedForId === current.id ? "Open again" : "Open in LinkedIn"}
            </button>
            <button
              className={openedForId === current.id ? "btn-success btn-sm" : "btn-secondary btn-sm"}
              disabled={busy}
              onClick={markSent}
            >
              I sent it
            </button>
          </div>
        </div>
      )}

      {status && <div style={{ ...mutedText, marginTop: "10px" }}>{status}</div>}

      {/* ai: claude-opus-5-5 | 2026-10-26 -- everyone else still to go, so the whole queue is visible */}
      {upNext.length > 0 && (
        <div style={{ marginTop: "16px" }}>
          <div style={{ fontWeight: 600, marginBottom: "6px" }}>Up next ({upNext.length.toLocaleString()})</div>
          <ul aria-label="Up next" style={upNextList}>
            {upNext.map((c, i) => (
              <li key={c.id} style={i === 0 ? { ...upNextRow, borderTop: "none" } : upNextRow}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{c.full_name}</span>
                  <span style={{ ...mutedText, marginLeft: "8px" }}>
                    {[c.position, c.company_raw].filter(Boolean).join(" at ") || "No title or company"}
                  </span>
                </div>
                <span style={mutedText} title="Weighted outreach priority">
                  {c.outreach_score?.toFixed(1) ?? "—"}
                </span>
                <button
                  className="btn-secondary btn-xs"
                  disabled={busy}
                  onClick={() => jumpTo(c)}
                  aria-label={`Message ${c.full_name} now`}
                >
                  Message now
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ai: claude-opus-5-5 | 2026-10-26
const upNextList: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  maxHeight: "360px",
  overflowY: "auto",
  border: "1px solid var(--border)",
  borderRadius: "6px",
};

const upNextRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "6px 10px",
  borderTop: "1px solid var(--border)",
};

const headerRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
};

const mutedText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
};

const actionRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  marginTop: "8px",
  flexWrap: "wrap",
};
