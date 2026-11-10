/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Shown instead of the app to a signed-in user who has not accepted the current Terms
// of Service: an account made before the terms existed, or before they last changed
// (/api/session's terms_acceptance_required). Accepting records the version shown here
// (POST /api/account/accept-terms); the alternative is signing out.
import { useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import PublicPageShell from "./PublicPageShell.tsx";
import { TermsOfServiceText } from "./TermsOfServicePage.tsx";
import { TERMS_VERSION } from "./publicSite.ts";
import { apiFetch } from "../../utils/apiClient.ts";
import { describeFetchError } from "../../utils/fetchErrorUtils.ts";
import type { ApiErrorBody } from "../../types/common.ts";

const styles: Record<string, CSSProperties> = {
  intro: { fontSize: "14px", lineHeight: 1.6, margin: "0 0 16px" },
  terms: {
    maxHeight: "50vh",
    overflowY: "auto",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "16px 20px",
    marginBottom: "18px",
  },
  agree: { display: "flex", gap: "8px", alignItems: "flex-start", fontSize: "14px", marginBottom: "14px" },
  actions: { display: "flex", gap: "10px", flexWrap: "wrap" },
  error: { color: "var(--red)", fontSize: "13px", marginBottom: "12px" },
};

interface AcceptTermsPageProps {
  onAccepted: () => void;
  onLogout: () => void;
}

export default function AcceptTermsPage({ onAccepted, onLogout }: AcceptTermsPageProps) {
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const accept = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const res = await apiFetch("/api/account/accept-terms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: TERMS_VERSION }),
      });
      if (!res.ok) {
        const data: ApiErrorBody = await res.json().catch(() => ({}));
        setError(data.error || `Request failed (HTTP ${res.status})`);
        return;
      }
      onAccepted();
    } catch (err) {
      setError(describeFetchError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PublicPageShell hideSignIn>
      <p style={styles.intro}>
        Before you continue, please read and accept our Terms of Service.
      </p>
      <div style={styles.terms}>
        <TermsOfServiceText />
      </div>
      <form onSubmit={accept}>
        {error && <div style={styles.error}>{error}</div>}
        <label style={styles.agree}>
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} required />
          <span>I have read and agree to the Terms of Service.</span>
        </label>
        <div style={styles.actions}>
          <button type="submit" className="btn-primary" disabled={!agreed || submitting}>
            {submitting ? "Saving…" : "Accept and continue"}
          </button>
          <button type="button" className="btn-secondary" onClick={onLogout}>
            Sign out
          </button>
        </div>
      </form>
    </PublicPageShell>
  );
}
