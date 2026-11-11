/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Add a contact by hand (POST /api/contacts): a recruiter who called or emailed and
// isn't a LinkedIn connection. A later LinkedIn import merges into them, never deletes.
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import { responseErrorText, sendJson } from "../../utils/contactsUtils.ts";
import { NewContactFields } from "./ContactFields.tsx";
import { emptyNewContact, newContactBody } from "./leadsShared.ts";

interface AddContactModalProps {
  onAdded: () => void;
  onClose: () => void;
}

export default function AddContactModal({ onAdded, onClose }: AddContactModalProps) {
  const [draft, setDraft] = useState(emptyNewContact());
  const [keepInformed, setKeepInformed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!draft.full_name.trim()) {
      setError("Enter their name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await sendJson("/api/contacts", "POST", { ...newContactBody(draft), keep_informed: keepInformed });
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="Add a contact"
      subtitle="Someone who reached out by phone, email or text"
      onClose={onClose}
      maxWidth="720px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
          <button type="button" className="btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Add contact"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <NewContactFields value={draft} onChange={setDraft} />
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={keepInformed} onChange={(e) => setKeepInformed(e.target.checked)} />
          Keep them posted on my search
        </label>
        {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
      </div>
    </Modal>
  );
}
