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

import { useEffect, useRef, useState } from "react";

const POLL_MS = 60_000;

const styles = {
  bar: {
    borderTop: "1px solid rgba(255,255,255,0.1)",
    background: "rgba(0,0,0,0.12)",
    padding: "0 28px",
  },
  inner: {
    display: "flex",
    alignItems: "center",
    gap: "0",
    maxWidth: "1200px",
    margin: "0 auto",
    minHeight: "30px",
    flexWrap: "wrap",
  },
  item: {
    display: "flex",
    alignItems: "center",
    gap: "5px",
    padding: "4px 14px 4px 0",
    fontSize: "11px",
    color: "rgba(255,255,255,0.7)",
    whiteSpace: "nowrap",
  },
  num: (tone) => ({
    fontWeight: "700",
    fontSize: "12px",
    color:
      tone === "green" ? "#4ade80"
      : tone === "red" ? "#f87171"
      : tone === "orange" ? "#fb923c"
      : tone === "accent" ? "rgba(255,255,255,0.95)"
      : "rgba(255,255,255,0.95)",
  }),
  sep: {
    color: "rgba(255,255,255,0.2)",
    margin: "0 2px 0 0",
    userSelect: "none",
  },
};

function StatItem({ value, label, tone, showSep }) {
  return (
    <>
      {showSep && <span style={styles.sep}>·</span>}
      <span style={styles.item}>
        <span style={styles.num(tone)}>{value ?? "—"}</span>
        <span>{label}</span>
      </span>
    </>
  );
}

export default function StatsBar() {
  const [stats, setStats] = useState(null);
  const intervalRef = useRef(null);

  const fetchStats = async () => {
    try {
      const res = await fetch("/api/jobs/stats");
      if (!res.ok) return;
      const data = await res.json();
      setStats(data);
    } catch {
      /* no-op */
    }
  };

  useEffect(() => {
    fetchStats();
    intervalRef.current = setInterval(fetchStats, POLL_MS);
    return () => clearInterval(intervalRef.current);
  }, []);

  if (!stats) return null;

  return (
    <div style={styles.bar}>
      <div style={styles.inner}>
        <StatItem value={stats.unique_jobs} label="unique" tone="accent" showSep={false} />
        <StatItem value={stats.in_queue} label="in queue" showSep />
        <StatItem value={stats.in_triage} label="in triage" showSep />
        <StatItem value={stats.applied} label="applied" tone="green" showSep />
        <StatItem value={stats.rejected} label="rejected" tone="red" showSep />
        <StatItem value={stats.ghosted} label="ghosted" tone="orange" showSep />
        <StatItem value={stats.duplicates} label="duplicates" showSep />
      </div>
    </div>
  );
}
