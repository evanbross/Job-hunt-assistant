/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 * You may read, study, and run this code for personal purposes.
 * You may NOT:
 *   - Use this code, in whole or in part, in any commercial product or service
 *   - Sell, sublicense, or otherwise monetize this code or derivatives of it
 *   - Redistribute this code without explicit written permission from ZombieTech LLC
 *
 * No warranty is provided, express or implied. Use at your own risk.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import Modal from "./common/Modal.tsx";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
import type { ContactsImportSummary, ContactsStatusResponse } from "../types/contacts.ts";

interface ContactsImportProps {
  onImported?: (() => void) | null;
}

// ai: claude-sonnet-5 | 2026-07-26
export default function ContactsImport({ onImported }: ContactsImportProps) {
  const [status, setStatus] = useState<ContactsStatusResponse | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<ContactsImportSummary | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadStatus = () =>
    fetch("/api/contacts/status")
      .then((r) => r.json())
      .then((data: ContactsStatusResponse) => setStatus(data))
      .catch(() => {});

  useEffect(() => {
    loadStatus();
  }, []);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError("Only CSV files are accepted.");
      return;
    }

    setError("");
    setSummary(null);
    setUploading(true);
    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/contacts/import", { method: "POST", body: form });
      const data: ContactsImportSummary & ApiErrorBody = await res.json();
      if (!res.ok) {
        setError(data.error || "Import failed");
        return;
      }
      setSummary(data);
      await loadStatus();
      onImported?.();
    } catch (e) {
      setError("Import failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div>
      <h3 style={sectionTitle}>LinkedIn Contacts</h3>

      <p style={{ color: "var(--text-muted)", fontSize: "13px", marginBottom: "12px" }}>
        Import your LinkedIn connections (Settings &gt; Data privacy &gt; Get a copy of
        your data &gt; Connections) to see who you already know at companies you're
        applying to. Re-importing updates the list in place: new connections are
        added, existing ones are refreshed, and anyone you've flagged, classified
        or emailed is always kept.
      </p>

      {status?.count ? (
        <div style={infoBox}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "24px" }}>🔗</span>
            <div>
              <div style={{ fontWeight: "600" }}>
                {status.count.toLocaleString()} contacts imported
              </div>
              {status.last_imported_at && (
                <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>
                  Last imported {formatLocalDateTime(status.last_imported_at)}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <p style={{ color: "var(--text-muted)", fontSize: "13px", marginBottom: "12px" }}>
          No contacts imported yet.
        </p>
      )}

      {summary && (
        <div style={{ ...infoBox, marginTop: "10px", fontSize: "12px" }}>
          Read {summary.imported} of {summary.total_rows} rows: {summary.added} new,
          {" "}{summary.updated} updated
          {summary.removed > 0 && `, ${summary.removed} no longer in your export removed`}
          {summary.skipped > 0 && ` (${summary.skipped} rows skipped)`}.
        </div>
      )}

      <div style={{ marginTop: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          onChange={handleFileChange}
          style={{ display: "none" }}
          id="contacts-file-input"
        />
        <label
          htmlFor="contacts-file-input"
          className="btn-secondary"
          style={{
            cursor: uploading ? "not-allowed" : "pointer",
            opacity: uploading ? 0.5 : 1,
            display: "inline-block",
            padding: "6px 14px",
            borderRadius: "var(--radius)",
            background: "var(--surface2)",
            border: "1px solid var(--border)",
          }}
        >
          {uploading ? "Importing…" : status?.count ? "Re-import CSV" : "Import CSV"}
        </label>

        {error && <span style={{ color: "var(--red)", fontSize: "12px" }}>{error}</span>}
      </div>

      {uploading && (
        // ai: claude-sonnet-5 | 2026-07-26
        <Modal title="Importing Contacts" onClose={() => {}} showCloseButton={false} maxWidth="420px">
          <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--text-muted)", fontSize: "14px" }}>
            <span className="inline-spinner" style={{ color: "var(--accent)" }} />
            <span>Importing your LinkedIn connections…</span>
          </div>
        </Modal>
      )}
    </div>
  );
}

const sectionTitle: CSSProperties = {
  fontSize: "13px",
  fontWeight: "700",
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginBottom: "12px",
};

const infoBox: CSSProperties = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  padding: "12px 14px",
};
