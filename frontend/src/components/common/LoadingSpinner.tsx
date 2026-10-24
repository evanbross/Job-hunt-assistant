/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
// Busy indicator for the waits where the app has nothing to show yet: the
// session check before the first render, and the Job Queue's initial list
// fetches. Uses the shared .inline-spinner CSS animation (a transform), which
// the browser runs off the main thread, so it keeps spinning even while a
// large list is being rendered and the page can't run any script.
interface LoadingSpinnerProps {
  label: string;
  /** Center in the whole viewport (pre-app session check) instead of the content area. */
  fullPage?: boolean;
}

export default function LoadingSpinner({ label, fullPage = false }: LoadingSpinnerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        alignItems: "center",
        display: "flex",
        flexDirection: "column",
        gap: "14px",
        justifyContent: "center",
        minHeight: fullPage ? "100vh" : "240px",
      }}
    >
      <span className="inline-spinner" style={{ width: "32px", height: "32px", borderWidth: "3px", color: "var(--accent)" }} />
      <p style={{ margin: 0, fontSize: "13px", color: "var(--text-muted)" }}>{label}</p>
    </div>
  );
}
