/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-07-27
import Modal from "./Modal.jsx";

const TAILOR_PHASES = [
  { key: "jd",         label: "Analyzing job description" },
  { key: "resume",     label: "Reviewing your resume" },
  { key: "crosscheck", label: "Cross-checking requirements" },
  { key: "generate",   label: "Generating suggestions" },
];

/** Progress dialog shown while a resume-tailoring generation is in flight. */
export default function TailorProgressModal({ job, phaseIdx }) {
  const subtitle = [job.job_title, job.company].filter(Boolean).join(" at ");
  return (
    <Modal
      title="Tailoring Your Resume"
      subtitle={subtitle || undefined}
      onClose={() => {}}
      showCloseButton={false}
      maxWidth="400px"
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {TAILOR_PHASES.map((phase, idx) => {
          const isDone = idx < phaseIdx;
          const isActive = idx === phaseIdx;
          return (
            <div
              key={phase.key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "12px",
                padding: "10px 14px",
                background: isActive
                  ? "var(--accent-light)"
                  : isDone
                  ? "var(--green-light)"
                  : "var(--surface2)",
                border: `1px solid ${
                  isActive
                    ? "rgba(74,92,247,0.2)"
                    : isDone
                    ? "rgba(14,158,110,0.2)"
                    : "var(--border)"
                }`,
                borderRadius: "var(--radius)",
                transition: "background 0.4s, border-color 0.4s",
              }}
            >
              <span style={{ width: "18px", height: "18px", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                {isDone ? (
                  <span style={{ color: "var(--green)", fontSize: "14px", fontWeight: 700, lineHeight: 1 }}>✓</span>
                ) : isActive ? (
                  <span className="inline-spinner" style={{ color: "var(--accent)", width: "14px", height: "14px", border: "2px solid currentColor", borderRightColor: "transparent" }} />
                ) : (
                  <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--border)", display: "inline-block" }} />
                )}
              </span>
              <span style={{
                fontSize: "13px",
                fontWeight: isActive ? 600 : 400,
                color: isActive ? "var(--accent)" : isDone ? "var(--green)" : "var(--text-muted)",
              }}>
                {phase.label}
              </span>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
