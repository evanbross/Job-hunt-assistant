/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-15
// "Seen N more times" for a tracked job: every later capture of its posting
// (backend/db/repositories/job_sightings.py), summarized per source, with the
// individual sightings behind a toggle. Given `sightings` (the lead drawer
// already has them) it renders those; given only `count` (a Job Queue card) it
// fetches them the first time it's opened.
import { useState } from "react";
import type { JobSighting, JobSightingsResponse } from "../../types/jobs.ts";
import { apiFetch } from "../../utils/apiClient.ts";
import { responseErrorText, safeHttpUrl } from "../../utils/contactsUtils.ts";
import { formatLocalDate } from "../../utils/dateTimeUtils.ts";

const CHANNEL_LABELS: Record<JobSighting["channel"], string> = {
  email: "email",
  extension: "browser extension",
  manual: "pasted",
  merged_duplicate: "earlier duplicate row",
};

interface SourceSummary {
  source: string;
  count: number;
  lastSeen: string;
}

function summarizeBySource(sightings: JobSighting[]): SourceSummary[] {
  const bySource = new Map<string, SourceSummary>();
  for (const sighting of sightings) {
    const entry = bySource.get(sighting.source);
    if (!entry) {
      bySource.set(sighting.source, { source: sighting.source, count: 1, lastSeen: sighting.seen_at });
    } else {
      entry.count += 1;
      if (sighting.seen_at > entry.lastSeen) entry.lastSeen = sighting.seen_at;
    }
  }
  return [...bySource.values()].sort((a, b) => b.count - a.count || a.source.localeCompare(b.source));
}

interface JobSightingsProps {
  jobId: number;
  /** Preloaded sightings (newest first); when given, nothing is fetched. */
  sightings?: JobSighting[];
  /** How many there are, when they haven't been loaded yet. */
  count?: number;
}

export default function JobSightings({ jobId, sightings: preloaded, count }: JobSightingsProps) {
  const [open, setOpen] = useState(false);
  const [fetched, setFetched] = useState<JobSighting[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const sightings = preloaded ?? fetched;
  const total = sightings ? sightings.length : count ?? 0;
  if (total === 0) return null;

  const toggle = async () => {
    const opening = !open;
    setOpen(opening);
    if (!opening || sightings || loading) return;
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch(`/api/jobs/${jobId}/sightings`);
      if (!res.ok) {
        setError(await responseErrorText(res));
        return;
      }
      const data: JobSightingsResponse = await res.json();
      setFetched(data.sightings);
    } catch {
      setError("Couldn't load sightings.");
    } finally {
      setLoading(false);
    }
  };

  const summary = sightings ? summarizeBySource(sightings) : [];

  return (
    <div className="job-sightings" style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
      <button
        type="button"
        className="link-button"
        onClick={toggle}
        aria-expanded={open}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--accent)", fontSize: "12px" }}
      >
        {open ? "▾" : "▸"} Seen {total} more time{total === 1 ? "" : "s"}
      </button>
      {summary.length > 0 && (
        <span style={{ marginLeft: "6px" }}>
          · {summary.map((s) => `${s.source}${s.count > 1 ? ` ×${s.count}` : ""}`).join(", ")}
        </span>
      )}
      {open && (
        <div style={{ marginTop: "4px" }}>
          {loading && <span>Loading…</span>}
          {error && <span style={{ color: "var(--red)" }}>{error}</span>}
          {sightings && (
            <ul style={{ margin: "4px 0 0", paddingLeft: "18px" }}>
              {sightings.map((sighting) => {
                const href = safeHttpUrl(sighting.url);
                return (
                  <li key={sighting.id}>
                    {formatLocalDate(sighting.seen_at)} · {sighting.source}
                    {" "}({CHANNEL_LABELS[sighting.channel] ?? sighting.channel})
                    {sighting.capture_label && <> · {sighting.capture_label}</>}
                    {href && (
                      <>
                        {" · "}
                        <a href={href} target="_blank" rel="noreferrer">link</a>
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
