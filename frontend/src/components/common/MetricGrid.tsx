/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */

// ai: claude-sonnet-5 | 2026-10-07
import type { ReactNode } from "react";

interface MetricItemProps {
  /** The metric value. */
  value: string | number;
  /** The metric label. */
  label: string;
  /** Optional color tone ("green", "red", "yellow", "accent"). */
  tone?: string;
}

/**
 * Standard Metric Item component for showing a single count/label pair.
 */
export function MetricItem({ value, label, tone }: MetricItemProps) {
  let color = "inherit";
  if (tone === "green") color = "var(--green)";
  if (tone === "red") color = "var(--red)";
  if (tone === "yellow") color = "var(--orange)";
  if (tone === "accent") color = "var(--accent)";

  return (
    <div className="metric-item">
      <div className="metric-value" style={{ color }}>
        {value}
      </div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

interface MetricGridProps {
  /** MetricItem components. */
  children: ReactNode;
}

/**
 * Standard Metric Grid component for showing multiple results in a dialog.
 */
export default function MetricGrid({ children }: MetricGridProps) {
  return <div className="metric-grid">{children}</div>;
}
