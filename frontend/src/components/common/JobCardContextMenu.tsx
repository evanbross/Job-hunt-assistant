/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-17
// The right-click menu every Job Queue card shares (JobCard, TriageCard,
// DuplicateCard). JobCard had the full menu inline while the Triage cards had
// only "Show Company History", with the description, cover letter, resume and
// pitch actions spread out as separate buttons; one component keeps the cards
// laid out the same way.
import type { CSSProperties } from "react";
import type { PitchType } from "../../types/coverLetter.ts";

interface MenuPosition {
  x: number;
  y: number;
}

interface JobCardContextMenuProps {
  position: MenuPosition;
  busy: boolean;
  /** Closes the menu, then runs the item's action (useCardContextMenu's runContextAction). */
  runAction: (action: () => void) => void;
  /** Omit to hide the show/hide description item (no description to show). */
  descriptionToggle?: { open: boolean; onToggle: () => void } | null;
  descriptionEditLabel: string;
  onEditDescription: () => void;
  coverLetterReady: boolean;
  onCoverLetter: () => void;
  resumeTailorReady: boolean;
  onTailorResume: () => void;
  onPitch: (type: PitchType) => void;
  historyAvailable: boolean;
  onShowCompanyHistory: () => void;
}

export default function JobCardContextMenu({
  position,
  busy,
  runAction,
  descriptionToggle,
  descriptionEditLabel,
  onEditDescription,
  coverLetterReady,
  onCoverLetter,
  resumeTailorReady,
  onTailorResume,
  onPitch,
  historyAvailable,
  onShowCompanyHistory,
}: JobCardContextMenuProps) {
  return (
    <div
      style={{ ...menuStyle, left: position.x, top: position.y }}
      onClick={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      {descriptionToggle && (
        <button type="button" style={itemStyle} onClick={() => runAction(descriptionToggle.onToggle)}>
          {descriptionToggle.open ? "Hide Description" : "Job Description"}
        </button>
      )}
      <button type="button" style={itemStyle} onClick={() => runAction(onEditDescription)} disabled={busy}>
        {descriptionEditLabel}
      </button>
      <div style={separatorStyle} />
      <button type="button" style={itemStyle} onClick={() => runAction(onCoverLetter)} disabled={busy}>
        {coverLetterReady ? "Show Cover Letter" : "Cover Letter"}
      </button>
      <button type="button" style={itemStyle} onClick={() => runAction(onTailorResume)} disabled={busy}>
        {resumeTailorReady ? "Show Resume Tailoring" : "Tailor Resume"}
      </button>
      <button type="button" style={itemStyle} onClick={() => runAction(() => onPitch("why_company"))} disabled={busy}>
        Why I want to work here
      </button>
      <button type="button" style={itemStyle} onClick={() => runAction(() => onPitch("why_fit"))} disabled={busy}>
        Why I&apos;d be good for this job
      </button>
      <div style={separatorStyle} />
      <button
        type="button"
        style={{ ...itemStyle, cursor: historyAvailable ? "pointer" : "not-allowed", opacity: historyAvailable ? 1 : 0.5 }}
        onClick={() => runAction(onShowCompanyHistory)}
        disabled={!historyAvailable}
        title={historyAvailable ? undefined : "No other jobs on record for this company"}
      >
        Show Company History
      </button>
    </div>
  );
}

const menuStyle: CSSProperties = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "var(--shadow-lg)",
  display: "flex",
  flexDirection: "column",
  minWidth: "220px",
  padding: "4px",
  position: "fixed",
  zIndex: 1000,
};

const itemStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  borderRadius: "4px",
  color: "var(--text)",
  cursor: "pointer",
  fontSize: "13px",
  padding: "8px 10px",
  textAlign: "left",
  width: "100%",
};

const separatorStyle: CSSProperties = {
  borderTop: "1px solid var(--border)",
  margin: "4px 0",
};
