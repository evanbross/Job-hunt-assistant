/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Import a LinkedIn data export's messages.csv (or the whole zip) into the suggested
// leads inbox (recruiter lead tracking, phase 2). The server keeps only conversations
// someone else started one-to-one, rules on what Contacts already knows, and -- if
// asked -- has the AI check the rest in the background.
import { useState } from "react";
import Modal from "../common/Modal.tsx";
import type { MessagesImportResponse } from "../../types/leads.ts";
import { responseErrorText } from "../../utils/contactsUtils.ts";

const ACCEPTED_FILES = ".csv,.zip";

const WINDOW_OPTIONS: { days: number; label: string }[] = [
  { days: 90, label: "the last 3 months" },
  { days: 180, label: "the last 6 months" },
  { days: 365, label: "the last year" },
  { days: 730, label: "the last 2 years" },
  { days: 0, label: "any time" },
];

const SKIP_LABELS: Record<keyof MessagesImportResponse["skipped"], string> = {
  started_by_you: "started by you",
  too_old: "quiet for longer than that",
  no_profile: "sponsored or LinkedIn messages",
  group: "group chats",
  spam: "in your spam folder",
};

interface ImportMessagesModalProps {
  onImported: () => void;
  onClose: () => void;
}

export default function ImportMessagesModal({ onImported, onClose }: ImportMessagesModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [days, setDays] = useState(365);
  const [useAi, setUseAi] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MessagesImportResponse | null>(null);

  const submit = async () => {
    if (!file) {
      setError("Choose messages.csv or the export's .zip file.");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("active_within_days", String(days));
    form.append("use_ai", useAi ? "true" : "false");
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/lead-suggestions/linkedin-messages", { method: "POST", body: form });
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      setResult((await res.json()) as MessagesImportResponse);
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    const skipped = (Object.keys(SKIP_LABELS) as (keyof typeof SKIP_LABELS)[])
      .filter((key) => result.skipped[key])
      .map((key) => `${result.skipped[key]} ${SKIP_LABELS[key]}`);
    return (
      <Modal
        title="LinkedIn messages imported"
        onClose={onClose}
        maxWidth="640px"
        footer={<button type="button" className="btn-primary" onClick={onClose}>Done</button>}
      >
        <div className="form-stack">
          <p style={{ margin: 0 }}>
            {/* ai: claude-opus-5-5 | 2026-11-16 -- one suggestion per person, so count people too */}
            {result.candidates} of {result.conversations} conversations could be someone reaching out, from{" "}
            {result.people} {result.people === 1 ? "person" : "people"} ({result.added} new,{" "}
            {result.refreshed} already imported).
          </p>
          {skipped.length > 0 && <p className="lead-sub" style={{ margin: 0 }}>Left out: {skipped.join(", ")}.</p>}
          {result.messages_logged > 0 && (
            <p style={{ margin: 0 }}>
              {result.messages_logged} newer message{result.messages_logged === 1 ? "" : "s"} logged on contacts you
              already accepted.
            </p>
          )}
          {result.waiting_for_ai > 0 && (
            <p style={{ margin: 0 }}>
              {result.check_started
                ? // ai: claude-opus-5-5 | 2026-11-16 -- a suggestion is a person now, not a conversation
                  `The AI is checking messages from ${result.waiting_for_ai} ${result.waiting_for_ai === 1 ? "person" : "people"} now; suggestions appear as it goes.`
                : `${result.waiting_for_ai} ${result.waiting_for_ai === 1 ? "person is" : "people are"} under “Not checked yet”.`}
            </p>
          )}
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="Import LinkedIn messages"
      subtitle="Find recruiters who reached out to you on LinkedIn"
      onClose={onClose}
      maxWidth="640px"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Importing…" : "Import"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <p className="lead-sub" style={{ margin: 0 }}>
          On LinkedIn: Settings → Data privacy → Get a copy of your data, and include Messages. When LinkedIn emails you the
          download, choose the .zip here (or messages.csv from inside it). Re-importing a newer export only adds what’s new.
        </p>
        <label>
          Export file ({ACCEPTED_FILES.replace(/,/g, ", ")})
          <input type="file" accept={ACCEPTED_FILES} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Conversations active in
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {WINDOW_OPTIONS.map((option) => (
              <option key={option.days} value={option.days}>{option.label}</option>
            ))}
          </select>
        </label>
        <label style={{ flexDirection: "row", alignItems: "flex-start", gap: 8 }}>
          <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
          <span>
            Use AI to check conversations Contacts can’t rule on
            <span className="lead-sub">
              Their opening messages are sent to your configured AI provider. Without it, they wait under “Not checked yet”.
            </span>
          </span>
        </label>
        {error && <p role="alert" style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
      </div>
    </Modal>
  );
}
