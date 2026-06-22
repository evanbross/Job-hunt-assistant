/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * LICENSE: Personal Use Only
 * This source code is made available for personal, non-commercial use only.
 */


/**
 * Standard Metric Item component for showing a single count/label pair.
 *
 * @param {Object} props
 * @param {string|number} props.value - The metric value.
 * @param {string} props.label - The metric label.
 * @param {string} [props.tone] - Optional color tone ("green", "red", "yellow", "accent").
 */
export function MetricItem({ value, label, tone }) {
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

/**
 * Standard Metric Grid component for showing multiple results in a dialog.
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children - MetricItem components.
 */
export default function MetricGrid({ children }) {
  return <div className="metric-grid">{children}</div>;
}
