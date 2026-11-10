/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-10
// Header and footer shared by the pages anyone can open without signing in.
import type { CSSProperties, ReactNode } from "react";
import { APP_NAME, COMPANY_NAME, HOME_PATH, LOGIN_PATH, PRIVACY_CONTACT_EMAIL, PRIVACY_PATH } from "./publicSite.ts";

const styles: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--text)" },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "16px",
    padding: "14px 20px",
    background: "var(--surface)",
    borderBottom: "1px solid var(--border)",
  },
  brand: { fontSize: "17px", fontWeight: 700, color: "var(--text)", textDecoration: "none" },
  main: { flex: 1, width: "100%", maxWidth: "760px", margin: "0 auto", padding: "32px 20px 48px", boxSizing: "border-box" },
  footer: {
    padding: "18px 20px",
    borderTop: "1px solid var(--border)",
    background: "var(--surface)",
    fontSize: "12.5px",
    color: "var(--text-muted)",
    display: "flex",
    flexWrap: "wrap",
    gap: "6px 18px",
    justifyContent: "center",
  },
  footerLink: { color: "var(--text-muted)" },
};

interface PublicPageShellProps {
  children: ReactNode;
  /** Hide the header's "Sign in" link (e.g. on a page that already is the way in). */
  hideSignIn?: boolean;
}

export default function PublicPageShell({ children, hideSignIn = false }: PublicPageShellProps) {
  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <a href={HOME_PATH} style={styles.brand}>💼 {APP_NAME}</a>
        {!hideSignIn && (
          <a href={LOGIN_PATH} className="btn-primary" style={{ textDecoration: "none" }}>Sign in</a>
        )}
      </header>
      <main style={styles.main}>{children}</main>
      <PublicFooter />
    </div>
  );
}

/** The footer alone, for pages with their own layout (the login page). */
export function PublicFooter() {
  return (
    <footer style={styles.footer}>
      <span>© {new Date().getFullYear()} {COMPANY_NAME}</span>
      <a href={PRIVACY_PATH} style={styles.footerLink}>Privacy policy</a>
      <a href={`mailto:${PRIVACY_CONTACT_EMAIL}`} style={styles.footerLink}>{PRIVACY_CONTACT_EMAIL}</a>
    </footer>
  );
}
