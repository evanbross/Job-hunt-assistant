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
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { apiFetch } from "../utils/apiClient.ts";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
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

  const loadTokens = async () => {
    try {
      const res = await apiFetch("/api/extension-tokens");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ExtensionTokensListResponse = await res.json();
      setTokens(data.tokens);
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

        <div style={createRow}>
          <label style={fieldLabel}>
            Name
            <input
              value={label}
              maxLength={100}
              placeholder="e.g. Brave on my desktop"
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <label style={fieldLabel}>
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

        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Token</th>
              <th style={thStyle}>Created</th>
              <th style={thStyle}>Last used</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle} />
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => {
              const status = tokenStatus(token);
              return (
                <tr key={token.id}>
                  <td style={tdStyle}>{token.label}</td>
                  <td style={tdStyle}>
                    <code>{token.token_prefix}…</code>
                  </td>
                  <td style={tdStyle}>{formatLocalDateTime(token.created_at)}</td>
                  <td style={tdStyle}>
                    {token.last_used_at ? formatLocalDateTime(token.last_used_at) : "Never"}
                  </td>
                  <td style={tdStyle}>
                    <span className={`badge ${status.className}`}>{status.label}</span>
                    {token.expires_at && !token.revoked_at && (
                      <div style={{ color: "var(--text-muted)", fontSize: "11px" }}>
                        until {formatLocalDateTime(token.expires_at)}
                      </div>
                    )}
                  </td>
                  <td style={tdStyle}>
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
                <td style={tdStyle} colSpan={6}>
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

const createRow: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "flex-end",
  gap: "12px",
  marginBottom: "18px",
};

const fieldLabel: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "4px",
  fontSize: "12px",
  fontWeight: 600,
  color: "var(--text-muted)",
};

const tableStyle: CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: "13px" };

const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  fontSize: "11px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderBottom: "1px solid var(--border)",
};

const tdStyle: CSSProperties = { padding: "8px", borderBottom: "1px solid var(--border)", verticalAlign: "top" };
