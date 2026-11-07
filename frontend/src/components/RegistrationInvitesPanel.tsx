/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-07
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { apiFetch } from "../utils/apiClient.ts";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
import { listFieldLabelStyle, listFormRowStyle, listTableStyle, listTdStyle, listThStyle } from "./settingsStyles.ts";
import type { RegistrationInvite, RegistrationInvitesResponse } from "../types/auth.ts";

const INVITES_URL = "/api/settings/system/registration-invites";

/**
 * Admin section: which emails may create an account while registration is
 * invite-only (the registration_invite_only system setting, on by default).
 * Inviting sends nothing; the admin tells the person, who registers normally.
 * An invite is used up when that registration completes.
 */
export default function RegistrationInvitesPanel() {
  const [invites, setInvites] = useState<RegistrationInvite[]>([]);
  const [inviteOnly, setInviteOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [email, setEmail] = useState("");
  const [adding, setAdding] = useState(false);

  const loadInvites = async () => {
    try {
      const res = await apiFetch(INVITES_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: RegistrationInvitesResponse = await res.json();
      setInvites(data.invites);
      setInviteOnly(data.invite_only);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load invites: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadInvites();
  }, []);

  const addInvite = async () => {
    if (!email.trim()) {
      window.alert("Enter the email address to invite.");
      return;
    }
    setAdding(true);
    try {
      const res = await apiFetch(INVITES_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        window.alert(data.error || `Could not add the invite (HTTP ${res.status})`);
        return;
      }
      setEmail("");
      await loadInvites();
    } catch (e) {
      window.alert(`Could not add the invite: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAdding(false);
    }
  };

  const revokeInvite = async (invite: RegistrationInvite) => {
    if (!window.confirm(`Revoke the invite for ${invite.email}? A registration they already started will stop working.`)) {
      return;
    }
    try {
      const res = await apiFetch(`${INVITES_URL}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: invite.email }),
      });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        window.alert(data.error || `Could not revoke the invite (HTTP ${res.status})`);
        return;
      }
      await loadInvites();
    } catch (e) {
      window.alert(`Could not revoke the invite: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div>
      <h2 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "16px" }}>Invites</h2>
      <p style={helpText}>
        While registration is invite-only, only the emails listed here can create an account. Inviting
        someone sends nothing: tell them to register with that address. Each invite is used up once
        that account is created.
      </p>
      {!loading && !inviteOnly && (
        <div style={warningBox} role="status">
          Invite-only registration is off, so anyone can register right now. Turn it on under
          System &gt; General.
        </div>
      )}

      <div style={{ maxWidth: "760px" }}>
        <div style={listFormRowStyle}>
          <label style={listFieldLabelStyle}>
            Email
            <input
              type="email"
              value={email}
              maxLength={320}
              placeholder="friend@example.com"
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") addInvite();
              }}
            />
          </label>
          <button className="btn-primary" onClick={addInvite} disabled={adding}>
            {adding ? "Adding…" : "Invite"}
          </button>
        </div>

        {loadError && <div style={{ color: "var(--red)", marginBottom: "10px" }}>{loadError}</div>}

        <table style={listTableStyle}>
          <thead>
            <tr>
              <th style={listThStyle}>Email</th>
              <th style={listThStyle}>Invited</th>
              <th style={listThStyle} />
            </tr>
          </thead>
          <tbody>
            {invites.map((invite) => (
              <tr key={invite.email}>
                <td style={listTdStyle}>{invite.email}</td>
                <td style={listTdStyle}>{formatLocalDateTime(invite.created_at)}</td>
                <td style={listTdStyle}>
                  <button className="btn-danger btn-xs" onClick={() => revokeInvite(invite)}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
            {!loading && invites.length === 0 && (
              <tr>
                <td style={listTdStyle} colSpan={3}>
                  No open invites.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const helpText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginBottom: "14px",
  maxWidth: "640px",
};

const warningBox: CSSProperties = {
  background: "var(--orange-light)",
  border: "1px solid var(--orange)",
  borderRadius: "var(--radius)",
  padding: "10px 14px",
  marginBottom: "16px",
  fontSize: "13px",
  maxWidth: "760px",
};

