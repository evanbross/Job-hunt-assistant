/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Settings > Account > Your data: download everything the account holds, and delete
// the account. The routes (routes/session_auth.py: account/export, request-deletion,
// cancel-deletion) existed with no way to reach them from the app; the privacy
// policy tells users to come here.
import { useState } from "react";
import type { FormEvent } from "react";
import { apiFetch } from "../utils/apiClient.ts";
import { describeFetchError } from "../utils/fetchErrorUtils.ts";
import { formBox, helpTextStyle, labelStyle, sectionSubheadingStyle } from "./settingsStyles.ts";
import type { AccountInfo } from "../types/auth.ts";
import type { ApiErrorBody } from "../types/common.ts";

const EXPORT_FILENAME = "job-hunt-assistant-export.json";

interface AccountDataSectionProps {
  account: AccountInfo;
  onDeletionChange: (deletionRequestedAt: string | null) => void;
}

/** The date the account is permanently deleted, for a request made at `requestedAt`. */
function permanentDeletionDate(requestedAt: string, graceDays: number): string {
  const when = new Date(requestedAt);
  when.setDate(when.getDate() + graceDays);
  return when.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

export default function AccountDataSection({ account, onDeletionChange }: AccountDataSectionProps) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [cancelling, setCancelling] = useState(false);

  const downloadExport = async () => {
    setExporting(true);
    setExportError("");
    try {
      const res = await apiFetch("/api/account/export");
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        setExportError(data.error || `Download failed (HTTP ${res.status})`);
        return;
      }
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = EXPORT_FILENAME;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(describeFetchError(err));
    } finally {
      setExporting(false);
    }
  };

  const requestDeletion = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (
      !window.confirm(
        `Delete your account? Everything in it is permanently deleted after ${account.deletion_grace_days} days. ` +
          "You can cancel any time before then.",
      )
    ) {
      return;
    }
    setDeleting(true);
    setDeleteError("");
    try {
      const res = await apiFetch("/api/account/request-deletion", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const data: { deletion_requested_at?: string } & ApiErrorBody = await res.json().catch(() => ({}));
      if (!res.ok || !data.deletion_requested_at) {
        setDeleteError(data.error || `Request failed (HTTP ${res.status})`);
        return;
      }
      setPassword("");
      onDeletionChange(data.deletion_requested_at);
    } catch (err) {
      setDeleteError(describeFetchError(err));
    } finally {
      setDeleting(false);
    }
  };

  const cancelDeletion = async () => {
    setCancelling(true);
    setDeleteError("");
    try {
      const res = await apiFetch("/api/account/cancel-deletion", { method: "POST" });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        setDeleteError(data.error || `Request failed (HTTP ${res.status})`);
        return;
      }
      onDeletionChange(null);
    } catch (err) {
      setDeleteError(describeFetchError(err));
    } finally {
      setCancelling(false);
    }
  };

  return (
    <>
      <h3 style={{ ...sectionSubheadingStyle, marginTop: "24px" }}>Your data</h3>
      <div style={formBox}>
        <p style={{ margin: "0 0 12px 0", fontSize: "13px", color: "var(--text)" }}>
          Download everything stored for your account (jobs, applications, contacts, profile,
          settings and history) as one JSON file. Passwords, API keys and Google sign-in tokens
          are left out.
        </p>
        <button className="btn-secondary" onClick={downloadExport} disabled={exporting}>
          {exporting ? "Preparing…" : "Download my data"}
        </button>
        {exportError && <div style={{ color: "var(--red)", fontSize: "13px", marginTop: "10px" }}>{exportError}</div>}
      </div>

      <h3 style={{ ...sectionSubheadingStyle, marginTop: "24px" }}>Delete account</h3>
      <div style={formBox}>
        {deleteError && <div style={{ color: "var(--red)", fontSize: "13px", marginBottom: "10px" }}>{deleteError}</div>}
        {account.deletion_requested_at ? (
          <>
            <p style={{ margin: "0 0 12px 0", fontSize: "13px", color: "var(--text)" }}>
              Your account is scheduled for permanent deletion on{" "}
              <strong>{permanentDeletionDate(account.deletion_requested_at, account.deletion_grace_days)}</strong>.
              Until then everything keeps working, and you can cancel.
            </p>
            <button className="btn-primary" onClick={cancelDeletion} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel deletion"}
            </button>
          </>
        ) : (
          <form onSubmit={requestDeletion}>
            <p style={{ margin: "0 0 12px 0", fontSize: "13px", color: "var(--text)" }}>
              Deletes your account and everything in it, and revokes the app's access to your
              Gmail. It becomes permanent {account.deletion_grace_days} days after you ask; you can
              cancel until then. Download your data first if you want a copy.
            </p>
            <label style={labelStyle}>
              Current password
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <span style={helpTextStyle}>Confirms it's really you.</span>
            </label>
            <button type="submit" className="btn-danger" style={{ marginTop: "12px" }} disabled={deleting}>
              {deleting ? "Requesting…" : "Delete my account"}
            </button>
          </form>
        )}
      </div>
    </>
  );
}
