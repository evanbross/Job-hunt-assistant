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

// ai: claude-sonnet-5 | 2026-10-07
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type {
  RecruiterType,
  TitleReportRow,
  TitleSuggestResponse,
  TitleSuggestion,
  TitlesReportResponse,
} from "../types/contacts.ts";

const PAGE_SIZE = 100;

// ai: claude-sonnet-5 | 2026-09-30
// Matches backend/db/repositories/contacts.py's recruiter_type CHECK
// constraint. Mirrors ContactsPanel.tsx's RECRUITER_TYPES.
const RECRUITER_TYPES: { value: RecruiterType; label: string }[] = [
  { value: "unrated", label: "Unrated" },
  { value: "motion", label: "Motion (high-pressure, low-success)" },
  { value: "low_reliability", label: "Low Reliability" },
  { value: "high_reliability", label: "High Reliability" },
  { value: "offshore_spam", label: "Offshore spam" },
];

const QUICK_FILTERS = ["recruit", "talent", "sourc"];

// ai: claude-sonnet-5 | 2026-09-28
async function sendJson(url: string, method: string, body?: unknown): Promise<Response> {
  return fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

interface TitlesReportProps {
  onViewContactsForTitle?: (normalizedTitle: string) => void;
}

// ai: claude-sonnet-5 | 2026-09-28
// docs/contacts-recruiter-classification-plan.md build-order item 3: one row
// per distinct normalized title, with stored title rules a person can flag
// or dismiss in bulk (decided_by provenance keeps this from ever overwriting
// a manual per-contact choice -- see the backend).
export default function TitlesReport({ onViewContactsForTitle }: TitlesReportProps) {
  const [titles, setTitles] = useState<TitleReportRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [unflaggedOnly, setUnflaggedOnly] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [pendingType, setPendingType] = useState<Record<string, RecruiterType>>({});
  const [busyTitle, setBusyTitle] = useState<string | null>(null);
  const [useAi, setUseAi] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestError, setSuggestError] = useState("");
  const [suggestions, setSuggestions] = useState<TitleSuggestion[] | null>(null); // null = no review panel open
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(() => new Set());
  const [applyingSuggestions, setApplyingSuggestions] = useState(false);

  const loadTitles = async () => {
    try {
      const res = await fetch("/api/contacts/titles");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TitlesReportResponse = await res.json();
      setTitles(data.titles);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load titles: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTitles();
  }, []);

  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [search, unflaggedOnly]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return titles.filter((t) => {
      if (unflaggedOnly && t.unflagged_count === 0) return false;
      if (!needle) return true;
      return t.title.toLowerCase().includes(needle);
    });
  }, [titles, search, unflaggedOnly]);

  const visible = filtered.slice(0, visibleCount);

  const applyRule = async (normalizedTitle: string, recruiterType: RecruiterType | null) => {
    setBusyTitle(normalizedTitle);
    try {
      const res = await sendJson("/api/contacts/titles/rule", "POST", {
        normalized_title: normalizedTitle,
        recruiter_type: recruiterType,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadTitles();
    } catch (e) {
      window.alert(`Could not save the rule: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyTitle(null);
    }
  };

  const undoRule = async (normalizedTitle: string) => {
    setBusyTitle(normalizedTitle);
    try {
      const res = await sendJson("/api/contacts/titles/rule/undo", "POST", {
        normalized_title: normalizedTitle,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadTitles();
    } catch (e) {
      window.alert(`Could not undo the rule: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyTitle(null);
    }
  };

  // ai: claude-sonnet-5 | 2026-09-28
  // Tier 1/2 suggestions (docs/contacts-recruiter-classification-plan.md build
  // order item 4). Nothing is applied until the user reviews this list and
  // clicks "Flag selected as recruiters" -- see routes/contacts.py's
  // suggest_title_rules/apply_title_suggestions.
  const runSuggest = async () => {
    setSuggesting(true);
    setSuggestError("");
    try {
      const res = await sendJson("/api/contacts/titles/suggest", "POST", { use_ai: useAi });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TitleSuggestResponse = await res.json();
      setSuggestions(data.suggestions);
      setSelectedSuggestions(new Set(data.suggestions.map((s) => s.normalized_title)));
      if (data.ai_error) {
        setSuggestError(`AI suggestions unavailable: ${data.ai_error}`);
      }
    } catch (e) {
      setSuggestError(`Could not get suggestions: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSuggesting(false);
    }
  };

  const toggleSuggestion = (normalizedTitle: string, checked: boolean) =>
    setSelectedSuggestions((prev) => {
      const next = new Set(prev);
      if (checked) next.add(normalizedTitle);
      else next.delete(normalizedTitle);
      return next;
    });

  const applySuggestions = async () => {
    const normalizedTitles = [...selectedSuggestions];
    if (normalizedTitles.length === 0) return;
    setApplyingSuggestions(true);
    try {
      const res = await sendJson("/api/contacts/titles/suggest/apply", "POST", {
        normalized_titles: normalizedTitles,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSuggestions(null);
      await loadTitles();
    } catch (e) {
      window.alert(`Could not apply suggestions: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setApplyingSuggestions(false);
    }
  };

  return (
    <div>
      <p style={summaryStyle}>
        Every distinct job title among your contacts. Flag a title as a recruiter type (or
        dismiss it as not a recruiter) to decide everyone with that title who hasn't been
        decided individually -- a re-import applies the same decision to new connections
        automatically.
      </p>

      {loadError && <div style={{ color: "var(--red)", marginBottom: "12px" }}>{loadError}</div>}

      {!loading && titles.length > 0 && (
        <div style={suggestBar}>
          <button className="btn-secondary btn-sm" disabled={suggesting} onClick={runSuggest}>
            {suggesting ? "Checking titles…" : "Suggest recruiter titles"}
          </button>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            Also use AI for titles keyword rules can't decide
          </label>
        </div>
      )}

      {suggestError && (
        <div style={{ color: "var(--red)", marginBottom: "12px", fontSize: "13px" }}>
          {suggestError}
        </div>
      )}

      {suggestions !== null && (
        <div className="card" style={suggestPanel}>
          {suggestions.length === 0 ? (
            <div style={{ color: "var(--text-muted)" }}>
              No suggestions -- every remaining title is either already decided or wasn't a
              confident match.
            </div>
          ) : (
            <>
              <strong>
                {suggestions.length} title{suggestions.length === 1 ? "" : "s"} suggested as
                recruiters -- review before applying:
              </strong>
              <div style={{ maxHeight: "260px", overflowY: "auto", margin: "10px 0" }}>
                {suggestions.map((s) => (
                  <label key={s.normalized_title} style={suggestRow}>
                    <input
                      type="checkbox"
                      checked={selectedSuggestions.has(s.normalized_title)}
                      onChange={(e) => toggleSuggestion(s.normalized_title, e.target.checked)}
                    />
                    <span style={{ fontWeight: 600 }}>{s.title}</span>
                    <span style={subtleText}>{s.reason}</span>
                  </label>
                ))}
              </div>
            </>
          )}
          <div style={{ display: "flex", gap: "8px" }}>
            {suggestions.length > 0 && (
              <button
                className="btn-primary btn-sm"
                disabled={applyingSuggestions || selectedSuggestions.size === 0}
                onClick={applySuggestions}
              >
                {applyingSuggestions
                  ? "Applying…"
                  : `Flag ${selectedSuggestions.size} selected as recruiters`}
              </button>
            )}
            <button
              className="btn-secondary btn-sm"
              disabled={applyingSuggestions}
              onClick={() => setSuggestions(null)}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {!loading && titles.length > 0 && (
        <>
          <div style={toolbar}>
            <input
              type="search"
              placeholder="Search titles"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ flex: "1 1 220px", minWidth: "180px" }}
              aria-label="Search titles"
            />
            {QUICK_FILTERS.map((word) => (
              <button
                key={word}
                className={search === word ? "btn-primary btn-sm" : "btn-secondary btn-sm"}
                onClick={() => setSearch(search === word ? "" : word)}
              >
                {word}
              </button>
            ))}
            <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
              <input
                type="checkbox"
                checked={unflaggedOnly}
                onChange={(e) => setUnflaggedOnly(e.target.checked)}
              />
              Unflagged only
            </label>
          </div>

          <div style={{ color: "var(--text-muted)", fontSize: "12px", margin: "0 2px 8px" }}>
            {filtered.length.toLocaleString()} of {titles.length.toLocaleString()} titles
          </div>

          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            {visible.length > 0 && (
              <div style={{ ...rowGrid, ...headerRow }}>
                <span style={headerCell}>Title</span>
                <span style={headerCell}>Contacts</span>
                <span style={headerCell}>Flagged</span>
                <span style={headerCell}>Unflagged</span>
                <span style={headerCell}>Actions</span>
              </div>
            )}
            {visible.length === 0 ? (
              <div style={{ padding: "18px", color: "var(--text-muted)" }}>
                No titles match this search.
              </div>
            ) : (
              visible.map((t) => {
                const busy = busyTitle === t.normalized_title;
                return (
                  <div key={t.normalized_title} style={rowGrid}>
                    <div style={{ fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {t.title}
                      {t.rule_exists && (
                        <span style={{ ...subtleText, marginLeft: "6px" }}>
                          (rule: {t.rule_recruiter_type || "not a recruiter"})
                        </span>
                      )}
                    </div>
                    <button
                      className="btn-secondary btn-xs"
                      onClick={() => onViewContactsForTitle?.(t.normalized_title)}
                      title="Show these contacts in the Contacts list"
                    >
                      {t.contact_count.toLocaleString()}
                    </button>
                    <div>{t.flagged_count.toLocaleString()}</div>
                    <div>{t.unflagged_count.toLocaleString()}</div>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      <select
                        value={pendingType[t.normalized_title] || "unrated"}
                        onChange={(e) =>
                          setPendingType((prev) => ({
                            ...prev,
                            [t.normalized_title]: e.target.value as RecruiterType,
                          }))
                        }
                        aria-label={`Recruiter type to flag "${t.title}" as`}
                        style={{ fontSize: "12px" }}
                      >
                        {RECRUITER_TYPES.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <button
                        className="btn-primary btn-xs"
                        disabled={busy}
                        onClick={() =>
                          applyRule(t.normalized_title, pendingType[t.normalized_title] || "unrated")
                        }
                      >
                        Flag as recruiter
                      </button>
                      <button
                        className="btn-secondary btn-xs"
                        disabled={busy}
                        onClick={() => applyRule(t.normalized_title, null)}
                      >
                        Not a recruiter
                      </button>
                      {t.rule_exists && (
                        <button
                          className="btn-secondary btn-xs"
                          disabled={busy}
                          onClick={() => undoRule(t.normalized_title)}
                        >
                          Undo rule
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {filtered.length > visibleCount && (
            <div style={{ textAlign: "center", marginTop: "12px" }}>
              <button
                className="btn-secondary btn-sm"
                onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
              >
                Show {Math.min(PAGE_SIZE, filtered.length - visibleCount)} more
              </button>
            </div>
          )}
        </>
      )}

      {!loading && titles.length === 0 && !loadError && (
        <div style={{ padding: "18px", color: "var(--text-muted)" }} className="card">
          No titles yet -- import contacts to see them here.
        </div>
      )}

      {loading && <div style={{ color: "var(--text-muted)" }}>Loading titles…</div>}
    </div>
  );
}

const summaryStyle: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "12px",
  marginTop: "2px",
  marginBottom: "12px",
  maxWidth: "640px",
};

const toolbar: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "10px",
  marginBottom: "10px",
};

const suggestBar: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "14px",
  marginBottom: "10px",
};

const suggestPanel: CSSProperties = {
  marginBottom: "16px",
};

const suggestRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "10px",
  padding: "6px 0",
  fontSize: "13px",
};

const rowGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 90px 80px 90px minmax(260px, 340px)",
  alignItems: "center",
  gap: "12px",
  padding: "10px 14px",
  borderBottom: "1px solid var(--border)",
};

const headerRow: CSSProperties = {
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
  padding: "6px 14px",
};

const headerCell: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "6px",
  fontSize: "11px",
  fontWeight: 700,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const subtleText: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: "11px",
  fontWeight: 400,
};
