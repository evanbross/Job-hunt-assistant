/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-21
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  DISCARD_TEMPLATE_CHANGES_PROMPT,
  MAX_OUTREACH_MESSAGE_LENGTH,
  MAX_OUTREACH_TEMPLATE_TITLE_LENGTH,
  OUTREACH_PLACEHOLDER_HELP,
  byTemplateTitle,
  rememberedTemplateId,
  responseErrorText,
  sendJson,
} from "../utils/contactsUtils.ts";
import type { OutreachTemplate, OutreachTemplateListResponse } from "../types/contacts.ts";

const NEW_TEMPLATE = "new";

// ai: claude-opus-5-5 | 2026-10-26
interface OutreachTemplateManagerProps {
  /** Told whenever unsaved changes appear or go away (and false on unmount), so leaving the tab can ask first. */
  onDirtyChange: (dirty: boolean) => void;
}

// ai: claude-opus-5-5 | 2026-10-26 -- now the Contacts section's own
// "Templates" tab: loads the templates itself instead of being handed the
// outreach queue's list. The queue reloads them each time it opens.
/**
 * Create, edit, and delete the named LinkedIn message templates the outreach
 * queue picks from (e.g. "Close coworkers", "Recruiters"). Edits one template
 * at a time; switching away from unsaved changes asks first.
 */
export default function OutreachTemplateManager({ onDirtyChange }: OutreachTemplateManagerProps) {
  const [templates, setTemplates] = useState<OutreachTemplate[] | null>(null); // null until loaded
  const [loadError, setLoadError] = useState("");
  const [editingId, setEditingId] = useState<number | typeof NEW_TEMPLATE>(NEW_TEMPLATE);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  const saved = editingId === NEW_TEMPLATE ? null : ((templates ?? []).find((t) => t.id === editingId) ?? null);
  const dirty = title !== (saved?.title ?? "") || body !== (saved?.body ?? "");

  // ai: claude-opus-5-5 | 2026-10-26
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  // ai: claude-opus-5-5 | 2026-10-26
  // Reloading or closing the page (or leaving it) with unsaved edits gets the
  // browser's own "Leave site?" prompt. Only attached while dirty, so the page
  // never asks otherwise.
  useEffect(() => {
    if (!dirty) return undefined;
    const warnBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [dirty]);

  const open = (next: OutreachTemplate | null) => {
    setEditingId(next?.id ?? NEW_TEMPLATE);
    setTitle(next?.title ?? "");
    setBody(next?.body ?? "");
  };

  // ai: claude-opus-5-5 | 2026-10-26 -- opens on the template the queue last used
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
        const firstId = rememberedTemplateId(data.templates);
        open(data.templates.find((t) => t.id === firstId) ?? null);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(`Could not load your message templates: ${e instanceof Error ? e.message : String(e)}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ai: claude-opus-5-5 | 2026-10-26 -- driven by the list's Edit buttons (was a dropdown)
  const switchTo = (next: OutreachTemplate | null) => {
    if ((next?.id ?? NEW_TEMPLATE) === editingId) return;
    if (dirty && !window.confirm(DISCARD_TEMPLATE_CHANGES_PROMPT)) return;
    open(next);
  };

  const save = async () => {
    setBusy(true);
    try {
      const payload = { title, body };
      const res = saved
        ? await sendJson(`/api/outreach-templates/${saved.id}`, "PUT", payload)
        : await sendJson("/api/outreach-templates", "POST", payload);
      if (!res.ok) throw new Error(await responseErrorText(res));
      const template: OutreachTemplate = await res.json();
      setTemplates((prev) => [...(prev ?? []).filter((t) => t.id !== template.id), template].sort(byTemplateTitle));
      open(template);
      setStatus(`Saved the "${template.title}" template.`);
    } catch (e) {
      window.alert(`Could not save the template: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  // ai: claude-opus-5-5 | 2026-10-26 -- deletes any listed template, not just the one being edited
  const remove = async (target: OutreachTemplate) => {
    const editingTarget = target.id === editingId;
    const lostEdits = editingTarget && dirty ? " Your unsaved changes to it will be lost too." : "";
    if (!window.confirm(`Delete the "${target.title}" template?${lostEdits}`)) return;
    setBusy(true);
    try {
      const res = await sendJson(`/api/outreach-templates/${target.id}`, "DELETE");
      if (!res.ok) throw new Error(await responseErrorText(res));
      const remaining = (templates ?? []).filter((t) => t.id !== target.id);
      setTemplates(remaining);
      // Deleting some other template leaves the editor (and any edits in it) alone.
      if (editingTarget) open(remaining[0] ?? null);
      setStatus(`Deleted the "${target.title}" template.`);
    } catch (e) {
      window.alert(`Could not delete the template: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <div className="card" style={mutedText}>{loadError}</div>;
  if (templates === null) return <div className="card" style={mutedText}>Loading templates…</div>;

  return (
    <div className="card" style={{ marginBottom: "16px" }}>
      <div style={{ fontWeight: 700 }}>Message templates</div>
      <div style={{ ...mutedText, marginBottom: "10px" }}>
        The outreach queue starts each LinkedIn message from one of these.
      </div>
      {/* ai: claude-opus-5-5 | 2026-10-26 -- every template listed with its own Edit/Delete (was an "Editing" dropdown) */}
      {templates.length === 0 ? (
        <div style={{ ...mutedText, marginBottom: "10px" }}>No templates yet. Create your first one below.</div>
      ) : (
        <ul aria-label="Message templates" style={templateList}>
          {templates.map((t) => {
            const current = t.id === editingId;
            return (
              <li key={t.id} aria-current={current ? "true" : undefined} style={current ? currentTemplateRow : templateRow}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{t.title}</div>
                  <div style={{ ...mutedText, ...singleLine }} title={t.body}>
                    {t.body}
                  </div>
                </div>
                {current ? (
                  <span className="badge badge-accent">Editing</span>
                ) : (
                  <button className="btn-secondary btn-sm" disabled={busy} onClick={() => switchTo(t)} aria-label={`Edit ${t.title}`}>
                    Edit
                  </button>
                )}
                <button className="btn-danger btn-sm" disabled={busy} onClick={() => remove(t)} aria-label={`Delete ${t.title}`}>
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "12px" }}>
        <div style={{ fontWeight: 600 }}>{saved ? `Editing "${saved.title}"` : "New template"}</div>
        <span style={{ flex: 1 }} />
        {saved && (
          <button className="btn-secondary btn-sm" disabled={busy} onClick={() => switchTo(null)}>
            + New template
          </button>
        )}
      </div>
      <input
        type="text"
        value={title}
        maxLength={MAX_OUTREACH_TEMPLATE_TITLE_LENGTH}
        placeholder="Template name, e.g. Close coworkers"
        onChange={(e) => setTitle(e.target.value)}
        aria-label="Template name"
        style={{ width: "100%", marginTop: "8px" }}
      />
      <textarea
        value={body}
        rows={10}
        maxLength={MAX_OUTREACH_MESSAGE_LENGTH}
        placeholder="Hi {first_name}, ..."
        onChange={(e) => setBody(e.target.value)}
        aria-label="Template message"
        style={{ width: "100%", resize: "vertical", marginTop: "6px" }}
      />
      <div style={{ ...mutedText, margin: "4px 0 8px" }}>{OUTREACH_PLACEHOLDER_HELP}</div>
      <div style={{ display: "flex", gap: "8px" }}>
        <button className="btn-primary btn-sm" disabled={busy || !title.trim() || !body.trim() || !dirty} onClick={save}>
          {saved ? "Save template" : "Create template"}
        </button>
        <button className="btn-secondary btn-sm" disabled={busy || !dirty} onClick={() => open(saved)}>
          Revert
        </button>
      </div>
      {status && <div style={{ ...mutedText, marginTop: "10px" }}>{status}</div>}
    </div>
  );
}

const mutedText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
};

// ai: claude-opus-5-5 | 2026-10-26
const templateList: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  border: "1px solid var(--border)",
  borderBottom: "none", // each row draws its own bottom border
  borderRadius: "6px",
  overflow: "hidden",
};

const templateRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  padding: "8px 10px",
  borderBottom: "1px solid var(--border)",
};

const currentTemplateRow: CSSProperties = {
  ...templateRow,
  background: "var(--accent-light)",
};

const singleLine: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
