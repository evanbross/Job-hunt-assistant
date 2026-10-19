/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-08-13
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { describeFetchError } from "../../utils/fetchErrorUtils.ts";
import type { PhraseBookEntry } from "../../types/coverLetter.ts";
import type { ApiErrorBody } from "../../types/common.ts";

const rowStyle: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "10px 12px",
  marginBottom: "8px",
};

const tagPillStyle: CSSProperties = {
  display: "inline-block",
  fontSize: "10px",
  fontWeight: "600",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "var(--accent)",
  background: "rgba(76,175,229,0.12)",
  borderRadius: "999px",
  padding: "2px 8px",
  marginBottom: "6px",
};

const fieldStyle: CSSProperties = {
  width: "100%",
  fontSize: "13px",
  padding: "7px 10px",
  borderRadius: "var(--radius)",
  border: "1px solid var(--border)",
  background: "var(--surface)",
  color: "var(--text)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

interface PhraseForm {
  tag: string;
  label: string;
  phrase_text: string;
}

const emptyForm: PhraseForm = { tag: "", label: "", phrase_text: "" };

interface PhraseBookManagerProps {
  onInsert?: ((text: string) => void) | null;
  onClose?: (() => void) | null;
  initialText?: string | null;
}

/**
 * List/add/edit/delete UI and CRUD logic for saved cover-letter "why this
 * company" phrases, tagged by theme (e.g. "Health care"). Presentation-only —
 * callers decide how to frame it (inside a Modal, or as a full page panel).
 *
 * Props: optional onInsert(text) — when provided, each entry gets a "Use
 * this" button that hands the phrase text back to the caller and calls
 * onClose. Optional onClose — paired with onInsert. Optional initialText —
 * prefills the "add a new phrase" text area.
 */
export default function PhraseBookManager({ onInsert, onClose, initialText }: PhraseBookManagerProps) {
  const [phrases, setPhrases] = useState<PhraseBookEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addForm, setAddForm] = useState<PhraseForm>({ ...emptyForm, phrase_text: initialText || "" });
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<PhraseForm>(emptyForm);

  const fetchPhrases = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/phrase-book");
      if (!res.ok) throw new Error(`Failed to load (${res.status})`);
      const data: PhraseBookEntry[] = await res.json();
      setPhrases(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(describeFetchError(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPhrases();
  }, []);

  const handleAdd = async () => {
    const tag = addForm.tag.trim();
    const label = addForm.label.trim();
    const phrase_text = addForm.phrase_text.trim();
    if (!tag || !label || !phrase_text) {
      window.alert("Tag, label, and text are all required.");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/phrase-book", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag, label, phrase_text }),
      });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      setAddForm(emptyForm);
      await fetchPhrases();
    } catch (e) {
      window.alert("Save failed: " + describeFetchError(e));
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (phrase: PhraseBookEntry) => {
    setEditingId(phrase.id);
    setEditForm({ tag: phrase.tag || "", label: phrase.label || "", phrase_text: phrase.phrase_text || "" });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditForm(emptyForm);
  };

  const handleEditSave = async (id: number) => {
    const tag = editForm.tag.trim();
    const label = editForm.label.trim();
    const phrase_text = editForm.phrase_text.trim();
    if (!tag || !label || !phrase_text) {
      window.alert("Tag, label, and text are all required.");
      return;
    }
    try {
      const res = await fetch(`/api/phrase-book/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag, label, phrase_text }),
      });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      cancelEdit();
      await fetchPhrases();
    } catch (e) {
      window.alert("Save failed: " + describeFetchError(e));
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this phrase-book entry?")) return;
    try {
      const res = await fetch(`/api/phrase-book/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        throw new Error(data.error || `Delete failed (${res.status})`);
      }
      await fetchPhrases();
    } catch (e) {
      window.alert("Delete failed: " + describeFetchError(e));
    }
  };

  return (
    <div>
      {loading && <p style={{ fontSize: "13px", color: "var(--text-muted)" }}>Loading…</p>}
      {error && <p style={{ fontSize: "13px", color: "var(--red, #e05252)" }}>{error}</p>}

      {!loading && !error && phrases.length === 0 && (
        <p style={{ fontSize: "13px", color: "var(--text-muted)" }}>
          No saved phrases yet. Add one below — next time you write something like a
          "why this company" paragraph you're proud of, save it here so future letters
          can reuse or adapt it.
        </p>
      )}

      {phrases.map((phrase) => (
        <div key={phrase.id} style={rowStyle}>
          {editingId === phrase.id ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <input
                type="text"
                value={editForm.tag}
                onChange={(e) => setEditForm((prev) => ({ ...prev, tag: e.target.value }))}
                placeholder="Tag (e.g. Health care)"
                style={fieldStyle}
              />
              <input
                type="text"
                value={editForm.label}
                onChange={(e) => setEditForm((prev) => ({ ...prev, label: e.target.value }))}
                placeholder="Short label"
                style={fieldStyle}
              />
              <textarea
                value={editForm.phrase_text}
                onChange={(e) => setEditForm((prev) => ({ ...prev, phrase_text: e.target.value }))}
                rows={4}
                style={{ ...fieldStyle, resize: "vertical" }}
              />
              <div style={{ display: "flex", gap: "6px" }}>
                <button className="btn-primary" style={{ fontSize: "11px", padding: "3px 10px" }} onClick={() => handleEditSave(phrase.id)}>
                  Save
                </button>
                <button className="btn-secondary" style={{ fontSize: "11px", padding: "3px 10px" }} onClick={cancelEdit}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <span style={tagPillStyle}>{phrase.tag}</span>
              <div style={{ fontSize: "13px", fontWeight: "600", color: "var(--text)", marginBottom: "4px" }}>
                {phrase.label}
              </div>
              <p style={{ fontSize: "13px", lineHeight: "1.6", color: "var(--text)", whiteSpace: "pre-wrap", margin: "0 0 8px 0" }}>
                {phrase.phrase_text}
              </p>
              <div style={{ display: "flex", gap: "6px" }}>
                {onInsert && (
                  <button
                    className="btn-primary"
                    style={{ fontSize: "11px", padding: "3px 10px" }}
                    onClick={() => { onInsert(phrase.phrase_text); onClose?.(); }}
                  >
                    Use this
                  </button>
                )}
                <button className="btn-secondary" style={{ fontSize: "11px", padding: "3px 10px" }} onClick={() => startEdit(phrase)}>
                  Edit
                </button>
                <button className="btn-secondary" style={{ fontSize: "11px", padding: "3px 10px" }} onClick={() => handleDelete(phrase.id)}>
                  Delete
                </button>
              </div>
            </>
          )}
        </div>
      ))}

      <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
        <p style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
          Add a new phrase
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <input
            type="text"
            value={addForm.tag}
            onChange={(e) => setAddForm((prev) => ({ ...prev, tag: e.target.value }))}
            placeholder="Tag (e.g. Health care, Startups, Energy)"
            style={fieldStyle}
          />
          <input
            type="text"
            value={addForm.label}
            onChange={(e) => setAddForm((prev) => ({ ...prev, label: e.target.value }))}
            placeholder="Short label (e.g. South Florida health care experience)"
            style={fieldStyle}
          />
          <textarea
            value={addForm.phrase_text}
            onChange={(e) => setAddForm((prev) => ({ ...prev, phrase_text: e.target.value }))}
            placeholder="The paragraph itself — use {{COMPANY}} or {{JOB_TITLE}} anywhere you'd otherwise name the company, so it fills in fresh each time instead of carrying over the original one"
            rows={4}
            style={{ ...fieldStyle, resize: "vertical" }}
          />
          <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: 0 }}>
            Paste your own original wording here, not the AI's rewritten version — this is
            meant to preserve your actual voice for future letters to reuse.
          </p>
          <button
            className="btn-primary"
            disabled={saving}
            onClick={handleAdd}
            style={{ fontSize: "12px", padding: "6px 12px", alignSelf: "flex-start" }}
          >
            {saving ? "Saving…" : "Save phrase"}
          </button>
        </div>
      </div>
    </div>
  );
}
