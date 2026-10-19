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
import { safeHttpUrl, sendJson } from "../utils/contactsUtils.ts";
import type { Contact, OutreachDraftResponse, OutreachTemplateResponse } from "../types/contacts.ts";

// LinkedIn's direct-message limit; matches routes/contacts.py's
// MAX_OUTREACH_MESSAGE_LENGTH (backend/contacts/outreach_drafts.py).
const MAX_MESSAGE_LENGTH = 8000;

const PLACEHOLDER_HELP = "Placeholders: {first_name}, {full_name}, {company}, {position}";

interface OutreachQueueProps {
  contacts: Contact[];
  /** Reload the contact list after a send is logged; the queue advances because that contact now has outreach. */
  onOutreachLogged: () => Promise<void>;
}

// ai: claude-opus-5-5 | 2026-10-14
// Contacts marked "Want to contact: Yes" with nothing logged yet, highest
// Priority first -- the same outreach_score the contacts table sorts by.
function queueOrder(a: Contact, b: Contact): number {
  return (b.outreach_score ?? 0) - (a.outreach_score ?? 0) || a.full_name.localeCompare(b.full_name) || a.id - b.id;
}

async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body?.error === "string" ? body.error : `HTTP ${res.status}`;
}

// ai: claude-opus-5-5 | 2026-10-14
/**
 * Walks the "want to contact" list one person at a time: prepares a LinkedIn
 * message from the saved template (optionally rewritten by AI), stages it so
 * the browser extension pastes it into that contact's LinkedIn message box,
 * then logs it as sent once the user confirms they pressed Send on LinkedIn.
 */
export default function OutreachQueue({ contacts, onOutreachLogged }: OutreachQueueProps) {
  const [template, setTemplate] = useState("");
  const [templateDraft, setTemplateDraft] = useState("");
  const [showTemplate, setShowTemplate] = useState(false);
  const [skippedIds, setSkippedIds] = useState<Set<number>>(() => new Set());
  const [draft, setDraft] = useState("");
  const [draftEdited, setDraftEdited] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [openedForId, setOpenedForId] = useState<number | null>(null);

  const pending = useMemo(
    () => contacts.filter((c) => c.interested === true && c.outreach.length === 0),
    [contacts],
  );
  const queue = useMemo(
    () => pending.filter((c) => safeHttpUrl(c.profile_url) && !skippedIds.has(c.id)).sort(queueOrder),
    [pending, skippedIds],
  );
  const missingProfileCount = pending.filter((c) => !safeHttpUrl(c.profile_url)).length;
  const current = queue[0] ?? null;
  const currentId = current?.id ?? null;
  const profileUrl = safeHttpUrl(current?.profile_url);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/contacts/outreach-template")
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<OutreachTemplateResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        setTemplate(data.template);
        setTemplateDraft(data.template);
      })
      .catch((e) => {
        if (!cancelled) setStatus(`Could not load your message template: ${e instanceof Error ? e.message : String(e)}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requestDraft = async (contactId: number, useAi: boolean): Promise<string> => {
    const res = await sendJson(`/api/contacts/${contactId}/outreach-draft`, "POST", { use_ai: useAi });
    if (!res.ok) throw new Error(await errorText(res));
    const data: OutreachDraftResponse = await res.json();
    return data.draft;
  };

  // A new contact at the head of the queue: resume the draft staged for them
  // earlier, if any, otherwise start from the template. Read-only request, so
  // a StrictMode double-run is harmless; `cancelled` drops a stale response
  // if the queue moved on before it arrived.
  useEffect(() => {
    if (currentId === null) return undefined;
    const savedDraft = contacts.find((c) => c.id === currentId)?.outreach_draft;
    setOpenedForId(null);
    setDraftEdited(Boolean(savedDraft));
    if (savedDraft) {
      setDraft(savedDraft);
      return undefined;
    }
    let cancelled = false;
    setDraft("");
    setDraftLoading(true);
    requestDraft(currentId, false)
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
    // not throw away the draft being worked on for the same person.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  const regenerate = async (useAi: boolean) => {
    if (currentId === null) return;
    if (draftEdited && !window.confirm("Replace your edited draft?")) return;
    setDraftLoading(true);
    setStatus(useAi ? "Asking the AI for a personalized draft…" : "");
    try {
      const text = await requestDraft(currentId, useAi);
      setDraft(text);
      setDraftEdited(useAi);
      setStatus("");
    } catch (e) {
      setStatus(`Could not build a draft: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDraftLoading(false);
    }
  };

  const saveTemplate = async () => {
    setBusy(true);
    try {
      const res = await sendJson("/api/contacts/outreach-template", "PUT", { template: templateDraft });
      if (!res.ok) throw new Error(await errorText(res));
      const data: OutreachTemplateResponse = await res.json();
      setTemplate(data.template);
      setTemplateDraft(data.template);
      setShowTemplate(false);
      setStatus("Template saved.");
      if (currentId !== null && !draftEdited) setDraft(await requestDraft(currentId, false));
    } catch (e) {
      window.alert(`Could not save the template: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
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
      if (!res.ok) throw new Error(await errorText(res));
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
      if (!res.ok) throw new Error(await errorText(res));
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
    setStatus(`Skipped ${current.full_name} for now.`);
  };

  return (
    <div className="card" style={{ marginBottom: "16px" }}>
      <div style={headerRow}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700 }}>LinkedIn outreach queue</div>
          <div style={mutedText}>
            {queue.length === 0
              ? "Nobody left to message."
              : `${queue.length.toLocaleString()} to go, highest priority first.`}
            {skippedIds.size > 0 && ` ${skippedIds.size} skipped this session.`}
            {missingProfileCount > 0 &&
              ` ${missingProfileCount} marked "Yes" have no LinkedIn profile URL and aren't listed.`}
          </div>
        </div>
        {skippedIds.size > 0 && (
          <button className="btn-secondary btn-sm" disabled={busy} onClick={() => setSkippedIds(new Set())}>
            Bring back skipped
          </button>
        )}
        <button className="btn-secondary btn-sm" onClick={() => setShowTemplate((open) => !open)}>
          {showTemplate ? "Hide template" : "Edit template"}
        </button>
      </div>

      {showTemplate && (
        <div style={templateBox}>
          <textarea
            value={templateDraft}
            rows={6}
            maxLength={MAX_MESSAGE_LENGTH}
            onChange={(e) => setTemplateDraft(e.target.value)}
            aria-label="LinkedIn message template"
            style={{ width: "100%", resize: "vertical" }}
          />
          <div style={{ ...mutedText, margin: "4px 0 8px" }}>{PLACEHOLDER_HELP}</div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="btn-primary btn-sm"
              disabled={busy || !templateDraft.trim() || templateDraft === template}
              onClick={saveTemplate}
            >
              Save template
            </button>
            <button className="btn-secondary btn-sm" disabled={busy} onClick={() => setTemplateDraft(template)}>
              Revert
            </button>
          </div>
        </div>
      )}

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

          <textarea
            value={draft}
            rows={7}
            maxLength={MAX_MESSAGE_LENGTH}
            disabled={draftLoading}
            placeholder={draftLoading ? "Preparing a draft…" : "Your message"}
            onChange={(e) => {
              setDraft(e.target.value);
              setDraftEdited(true);
            }}
            aria-label={`Message to ${current.full_name}`}
            style={{ width: "100%", resize: "vertical", marginTop: "10px" }}
          />
          <div style={{ ...mutedText, textAlign: "right" }}>
            {draft.length.toLocaleString()} / {MAX_MESSAGE_LENGTH.toLocaleString()}
          </div>

          <div style={actionRow}>
            <button className="btn-secondary btn-sm" disabled={busy || draftLoading} onClick={() => regenerate(true)}>
              Draft with AI
            </button>
            <button className="btn-secondary btn-sm" disabled={busy || draftLoading} onClick={() => regenerate(false)}>
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
    </div>
  );
}

const headerRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
};

const mutedText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
};

const templateBox: CSSProperties = {
  marginTop: "12px",
  padding: "10px 12px",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
};

const actionRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  marginTop: "8px",
  flexWrap: "wrap",
};
