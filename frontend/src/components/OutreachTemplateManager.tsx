/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-21
import { useState } from "react";
import type { CSSProperties } from "react";
import {
  MAX_OUTREACH_MESSAGE_LENGTH,
  MAX_OUTREACH_TEMPLATE_TITLE_LENGTH,
  OUTREACH_PLACEHOLDER_HELP,
  responseErrorText,
  sendJson,
} from "../utils/contactsUtils.ts";
import type { OutreachTemplate } from "../types/contacts.ts";

const NEW_TEMPLATE = "new";

interface OutreachTemplateManagerProps {
  templates: OutreachTemplate[];
  /** The template to open first (the one the queue is using), or null to start a new one. */
  initialId: number | null;
  onSaved: (template: OutreachTemplate) => void;
  onDeleted: (templateId: number) => void;
}

// ai: claude-opus-5-5 | 2026-10-21
/**
 * Create, edit, and delete the named LinkedIn message templates the outreach
 * queue picks from (e.g. "Close coworkers", "Recruiters"). Edits one template
 * at a time; switching away from unsaved changes asks first.
 */
export default function OutreachTemplateManager({ templates, initialId, onSaved, onDeleted }: OutreachTemplateManagerProps) {
  const initial = templates.find((t) => t.id === initialId) ?? null;
  const [editingId, setEditingId] = useState<number | typeof NEW_TEMPLATE>(initial?.id ?? NEW_TEMPLATE);
  const [title, setTitle] = useState(initial?.title ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [busy, setBusy] = useState(false);

  const saved = editingId === NEW_TEMPLATE ? null : (templates.find((t) => t.id === editingId) ?? null);
  const dirty = title !== (saved?.title ?? "") || body !== (saved?.body ?? "");

  const open = (next: OutreachTemplate | null) => {
    setEditingId(next?.id ?? NEW_TEMPLATE);
    setTitle(next?.title ?? "");
    setBody(next?.body ?? "");
  };

  const switchTo = (value: string) => {
    if (dirty && !window.confirm("Discard your unsaved changes to this template?")) return;
    open(value === NEW_TEMPLATE ? null : (templates.find((t) => t.id === Number(value)) ?? null));
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
      open(template);
      onSaved(template);
    } catch (e) {
      window.alert(`Could not save the template: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!saved || !window.confirm(`Delete the "${saved.title}" template?`)) return;
    setBusy(true);
    try {
      const res = await sendJson(`/api/outreach-templates/${saved.id}`, "DELETE");
      if (!res.ok) throw new Error(await responseErrorText(res));
      open(templates.find((t) => t.id !== saved.id) ?? null);
      onDeleted(saved.id);
    } catch (e) {
      window.alert(`Could not delete the template: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={box}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        <label style={mutedText} htmlFor="outreach-template-editing">
          Editing
        </label>
        <select
          id="outreach-template-editing"
          value={String(editingId)}
          disabled={busy}
          onChange={(e) => switchTo(e.target.value)}
        >
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.title}
            </option>
          ))}
          <option value={NEW_TEMPLATE}>+ New template</option>
        </select>
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
        rows={6}
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
        <span style={{ flex: 1 }} />
        {saved && (
          <button className="btn-danger btn-sm" disabled={busy} onClick={remove}>
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

const box: CSSProperties = {
  marginTop: "12px",
  padding: "10px 12px",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
};

const mutedText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
};
