/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-09
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { apiFetch } from "../utils/apiClient.ts";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
import { listTableStyle, listTdStyle, listThStyle } from "./settingsStyles.ts";
import type { AdminAccount, AdminAccountsResponse, TaskFailure, TaskFailuresResponse } from "../types/auth.ts";

const ACCOUNTS_URL = "/api/settings/system/accounts";

const STATUS_LABELS: Record<AdminAccount["status"], string> = {
  active: "Active",
  disabled: "Disabled",
  pending_verification: "Hasn't finished registering",
  deletion_scheduled: "Deletion scheduled",
};

/**
 * Admin section (plan 4.5): every account, with what an admin can do about one --
 * disable or re-enable it, resend a registration link that expired or never arrived,
 * and see its failed background tasks. Acting on an account never changes its data.
 */
export default function AccountsPanel() {
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [you, setYou] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [failuresFor, setFailuresFor] = useState<AdminAccount | null>(null);
  const [failures, setFailures] = useState<TaskFailure[]>([]);
  const [failuresError, setFailuresError] = useState("");
  const [failuresLoading, setFailuresLoading] = useState(false);

  const loadAccounts = async () => {
    try {
      const res = await apiFetch(ACCOUNTS_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AdminAccountsResponse = await res.json();
      setAccounts(data.accounts);
      setYou(data.you);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load accounts: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAccounts();
  }, []);

  /** POST an account action; returns true on success, after alerting on failure. */
  const postAction = async (account: AdminAccount, action: string, what: string): Promise<boolean> => {
    setBusyId(account.id);
    try {
      const res = await apiFetch(`${ACCOUNTS_URL}/${account.id}/${action}`, { method: "POST" });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        window.alert(data.error || `Could not ${what} (HTTP ${res.status})`);
        return false;
      }
      return true;
    } catch (e) {
      window.alert(`Could not ${what}: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      setBusyId(null);
    }
  };

  const setDisabled = async (account: AdminAccount, disable: boolean) => {
    if (
      disable &&
      !window.confirm(
        `Disable ${account.email}? They are signed out at once, can't log in, their browser ` +
          "extension stops working and scheduled scans skip them. Nothing is deleted, and you can re-enable it.",
      )
    ) {
      return;
    }
    if (await postAction(account, disable ? "disable" : "enable", disable ? "disable the account" : "enable the account")) {
      await loadAccounts();
    }
  };

  const resendLink = async (account: AdminAccount) => {
    if (!window.confirm(`Email ${account.email} a new registration link? The old link stops working.`)) return;
    if (await postAction(account, "resend-verification", "send the link")) {
      window.alert(`Sent a new registration link to ${account.email}.`);
    }
  };

  const showFailures = async (account: AdminAccount) => {
    setFailuresFor(account);
    setFailures([]);
    setFailuresError("");
    setFailuresLoading(true);
    try {
      const res = await apiFetch(`${ACCOUNTS_URL}/${account.id}/task-failures`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TaskFailuresResponse = await res.json();
      setFailures(data.failures);
    } catch (e) {
      setFailuresError(`Could not load failed tasks: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setFailuresLoading(false);
    }
  };

  return (
    <div>
      <h2 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "16px" }}>Accounts</h2>
      <p style={helpText}>
        Every account on this installation. Disabling one signs it out and stops all its work until you
        re-enable it; nothing is deleted. Failed tasks are background jobs (scans, imports) that stopped
        with an error, with personal details removed from the message.
      </p>

      {loadError && <div style={{ color: "var(--red)", marginBottom: "10px" }}>{loadError}</div>}

      <table style={listTableStyle}>
        <thead>
          <tr>
            <th style={listThStyle}>Email</th>
            <th style={listThStyle}>Name</th>
            <th style={listThStyle}>Role</th>
            <th style={listThStyle}>Status</th>
            <th style={listThStyle}>Created</th>
            <th style={listThStyle}>Last login</th>
            <th style={listThStyle} />
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.id}>
              <td style={listTdStyle}>
                {account.email}
                {account.id === you && <span style={mutedNote}> (you)</span>}
              </td>
              <td style={listTdStyle}>{account.name || ""}</td>
              <td style={listTdStyle}>{account.role}</td>
              <td style={listTdStyle}>{STATUS_LABELS[account.status]}</td>
              <td style={listTdStyle}>{account.created_at ? formatLocalDateTime(account.created_at) : "Unknown"}</td>
              <td style={listTdStyle}>{account.last_login_at ? formatLocalDateTime(account.last_login_at) : "Never"}</td>
              <td style={{ ...listTdStyle, whiteSpace: "nowrap" }}>
                <button className="btn-secondary btn-xs" onClick={() => showFailures(account)}>
                  Failed tasks
                </button>{" "}
                {account.status === "pending_verification" && (
                  <>
                    <button className="btn-secondary btn-xs" onClick={() => resendLink(account)} disabled={busyId === account.id}>
                      Resend link
                    </button>{" "}
                  </>
                )}
                {account.status === "disabled" ? (
                  <button className="btn-secondary btn-xs" onClick={() => setDisabled(account, false)} disabled={busyId === account.id}>
                    Enable
                  </button>
                ) : (
                  account.id !== you && (
                    <button className="btn-danger btn-xs" onClick={() => setDisabled(account, true)} disabled={busyId === account.id}>
                      Disable
                    </button>
                  )
                )}
              </td>
            </tr>
          ))}
          {!loading && accounts.length === 0 && (
            <tr>
              <td style={listTdStyle} colSpan={7}>
                No accounts.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {failuresFor && (
        <section style={{ marginTop: "20px" }} aria-label="Failed tasks">
          <h3 style={{ fontSize: "14px", fontWeight: 700, marginBottom: "8px" }}>
            Failed tasks for {failuresFor.email}{" "}
            <button className="btn-secondary btn-xs" onClick={() => setFailuresFor(null)}>
              Close
            </button>
          </h3>
          {failuresError && <div style={{ color: "var(--red)", marginBottom: "10px" }}>{failuresError}</div>}
          <table style={listTableStyle}>
            <thead>
              <tr>
                <th style={listThStyle}>When</th>
                <th style={listThStyle}>Task</th>
                <th style={listThStyle}>Error</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((failure) => (
                <tr key={failure.id}>
                  <td style={{ ...listTdStyle, whiteSpace: "nowrap" }}>{formatLocalDateTime(failure.failed_at)}</td>
                  <td style={listTdStyle}>{failure.task_name.split(".").pop()}</td>
                  <td style={listTdStyle}>
                    <strong>{failure.error_type}</strong>: {failure.error_message}
                  </td>
                </tr>
              ))}
              {!failuresLoading && !failuresError && failures.length === 0 && (
                <tr>
                  <td style={listTdStyle} colSpan={3}>
                    No failed tasks recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

const helpText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginBottom: "14px",
  maxWidth: "640px",
};

const mutedNote: CSSProperties = { color: "var(--text-muted)" };
