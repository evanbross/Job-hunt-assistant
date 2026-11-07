/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { apiFetch } from "../utils/apiClient.ts";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
import { listFieldLabelStyle, listFormRowStyle, listTableStyle, listTdStyle, listThStyle } from "./settingsStyles.ts";
import type { CreateExtensionTokenResponse, ExtensionToken, ExtensionTokensListResponse } from "../types/extensionAuth.ts";

const EXPIRY_OPTIONS = [
  { value: "", label: "Never expires" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
];

// ai: claude-sonnet-5 | 2026-09-26
function tokenStatus(token: ExtensionToken): { label: string; className: string } {
  if (token.revoked_at) return { label: "Revoked", className: "badge-gray" };
  if (token.expires_at && new Date(token.expires_at) <= new Date()) {
    return { label: "Expired", className: "badge-orange" };
  }
  return { label: "Active", className: "badge-green" };
}

// ai: claude-sonnet-5 | 2026-09-26
/**
 * Settings section for the logged-in user's browser-extension credentials.
 * A token is shown exactly once, right after it is created; afterwards only
 * its short prefix is available, so a lost token is replaced, not recovered.
 */
export default function BrowserExtensionPanel() {
  const [tokens, setTokens] = useState<ExtensionToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [label, setLabel] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("");
  const [creating, setCreating] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [listenerUrl, setListenerUrl] = useState<string | null>(null); // ai: claude-opus-5-5 | 2026-11-07

  const loadTokens = async () => {
    try {
      const res = await apiFetch("/api/extension-tokens");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ExtensionTokensListResponse = await res.json();
      setTokens(data.tokens);
      setListenerUrl(data.listener_url); // ai: claude-opus-5-5 | 2026-11-07
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load tokens: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTokens();
  }, []);

  const createToken = async () => {
    if (!label.trim()) {
      window.alert("Give the token a name, such as the browser or computer it is for.");
      return;
    }
    setCreating(true);
    try {
      const res = await apiFetch("/api/extension-tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: label.trim(),
          expires_in_days: expiresInDays === "" ? null : Number(expiresInDays),
        }),
      });
      const data: CreateExtensionTokenResponse & ApiErrorBody = await res.json();
      if (!res.ok) {
        window.alert(data.error || `Could not create the token (HTTP ${res.status})`);
        return;
      }
      setNewToken(data.token);
      setCopied(false);
      setLabel("");
      setExpiresInDays("");
      await loadTokens();
    } catch (e) {
      window.alert(`Could not create the token: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCreating(false);
    }
  };

  const revokeToken = async (token: ExtensionToken) => {
    if (!window.confirm(`Revoke "${token.label}"? Any browser using it will stop working until you give it a new token.`)) {
      return;
    }
    try {
      const res = await apiFetch(`/api/extension-tokens/${token.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadTokens();
    } catch (e) {
      window.alert(`Could not revoke the token: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const copyToken = async () => {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken);
      setCopied(true);
    } catch {
      window.alert("Could not copy automatically. Select the token and copy it manually.");
    }
  };

  return (
    <div>
      <h2 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "16px" }}>Browser Extension</h2>
      <p style={helpText}>
        The browser extension signs in with a token tied to your account, so what it captures and
        autofills is always yours. Create one token per browser or computer, paste it into the
        extension's options, and revoke any you stop using.
      </p>

      <div style={{ maxWidth: "760px" }}>
        {/* ai: claude-opus-5-5 | 2026-11-07 -- hosted only: the server knows its public address */}
        {listenerUrl && (
          <div style={{ ...listFieldLabelStyle, marginBottom: "16px" }}>
            Listener URL (enter this in the extension's options too)
            <code style={tokenCode}>{listenerUrl}</code>
          </div>
        )}
        {newToken && (
          <div style={newTokenBox} role="status">
            <strong>Copy your new token now. It will not be shown again.</strong>
            <div style={{ display: "flex", gap: "8px", marginTop: "8px", alignItems: "center" }}>
              <code style={tokenCode}>{newToken}</code>
              <button className="btn-secondary btn-sm" onClick={copyToken}>
                {copied ? "Copied" : "Copy"}
              </button>
              <button className="btn-secondary btn-sm" onClick={() => setNewToken(null)}>
                Done
              </button>
            </div>
          </div>
        )}

        <div style={listFormRowStyle}>
          <label style={listFieldLabelStyle}>
            Name
            <input
              value={label}
              maxLength={100}
              placeholder="e.g. Brave on my desktop"
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <label style={listFieldLabelStyle}>
            Expires
            <select value={expiresInDays} onChange={(e) => setExpiresInDays(e.target.value)}>
              {EXPIRY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-primary" onClick={createToken} disabled={creating}>
            {creating ? "Creating…" : "Create token"}
          </button>
        </div>

        {loadError && <div style={{ color: "var(--red)", marginBottom: "10px" }}>{loadError}</div>}

        <table style={listTableStyle}>
          <thead>
            <tr>
              <th style={listThStyle}>Name</th>
              <th style={listThStyle}>Token</th>
              <th style={listThStyle}>Created</th>
              <th style={listThStyle}>Last used</th>
              <th style={listThStyle}>Status</th>
              <th style={listThStyle} />
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => {
              const status = tokenStatus(token);
              return (
                <tr key={token.id}>
                  <td style={listTdStyle}>{token.label}</td>
                  <td style={listTdStyle}>
                    <code>{token.token_prefix}…</code>
                  </td>
                  <td style={listTdStyle}>{formatLocalDateTime(token.created_at)}</td>
                  <td style={listTdStyle}>
                    {token.last_used_at ? formatLocalDateTime(token.last_used_at) : "Never"}
                  </td>
                  <td style={listTdStyle}>
                    <span className={`badge ${status.className}`}>{status.label}</span>
                    {token.expires_at && !token.revoked_at && (
                      <div style={{ color: "var(--text-muted)", fontSize: "11px" }}>
                        until {formatLocalDateTime(token.expires_at)}
                      </div>
                    )}
                  </td>
                  <td style={listTdStyle}>
                    {!token.revoked_at && (
                      <button className="btn-danger btn-xs" onClick={() => revokeToken(token)}>
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {!loading && tokens.length === 0 && (
              <tr>
                <td style={listTdStyle} colSpan={6}>
                  No tokens yet. Create one to connect the browser extension.
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

const newTokenBox: CSSProperties = {
  background: "var(--green-light)",
  border: "1px solid var(--green)",
  borderRadius: "var(--radius)",
  padding: "12px 14px",
  marginBottom: "16px",
  fontSize: "13px",
};

const tokenCode: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflowWrap: "anywhere",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "6px",
  padding: "6px 8px",
  userSelect: "all",
};

