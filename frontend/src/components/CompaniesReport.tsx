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
import { sendJson } from "../utils/contactsUtils.ts";
import type {
  CompaniesReportResponse,
  CompanyReportRow,
  CompanyWorkforce,
} from "../types/contacts.ts";

// ai: claude-sonnet-5 | 2026-09-28
// Matches backend/db/repositories/companies.py's workforce CHECK constraint.
const WORKFORCE_OPTIONS: { value: CompanyWorkforce; label: string }[] = [
  { value: "unknown", label: "Unknown" },
  { value: "onshore", label: "Onshore" },
  { value: "offshore_hybrid", label: "Offshore hybrid" },
];

type CompanyPatchFields = Partial<Pick<CompanyReportRow, "is_agency" | "workforce" | "notes">>;

// ai: claude-sonnet-5 | 2026-09-28
// docs/contacts-recruiter-classification-plan.md build-order item 2: one row
// per company that has at least one recruiter-flagged contact, with agency
// and workforce set here and applying to everyone at that company.
export default function CompaniesReport() {
  const [companies, setCompanies] = useState<CompanyReportRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [notesDrafts, setNotesDrafts] = useState<Record<string, string>>({});
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Set<string>>(() => new Set());

  const loadCompanies = async () => {
    try {
      const res = await fetch("/api/contacts/companies");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: CompaniesReportResponse = await res.json();
      setCompanies(data.companies);
      setLoadError("");
    } catch (e) {
      setLoadError(`Could not load companies: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCompanies();
  }, []);

  useEffect(() => {
    setNotesDrafts((prev) => {
      const next = { ...prev };
      for (const company of companies) {
        if (!(company.company_normalized in next)) {
          next[company.company_normalized] = company.notes || "";
        }
      }
      return next;
    });
  }, [companies]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return companies;
    return companies.filter((c) =>
      [c.company_raw, c.company_normalized].some((field) =>
        (field || "").toLowerCase().includes(needle),
      ),
    );
  }, [companies, search]);

  const unclassifiedCount = useMemo(
    () => filtered.filter((c) => c.workforce === "unknown").length,
    [filtered],
  );

  // ai: claude-sonnet-5 | 2026-09-30
  // Semi-automated workforce lookup: opens a LinkedIn company search in a new
  // tab for the user to read themselves and judge -- no automated scraping or
  // navigation of linkedin.com, since that risks the user's real account
  // (see docs/contacts-recruiter-classification-plan.md's Tier 3 discussion).
  // A company drops out of the queue on its own once its workforce is set;
  // `skipped` only covers "I looked, still don't know" for this session.
  const openNextUnclassified = () => {
    const target = filtered.find(
      (c) => c.workforce === "unknown" && !skipped.has(c.company_normalized),
    );
    if (!target) return;
    const url = `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(target.company_raw)}`;
    window.open(url, "_blank", "noopener,noreferrer");
    setHighlighted(target.company_normalized);
    setSkipped((prev) => new Set(prev).add(target.company_normalized));
  };

  // Optimistic: flip the UI immediately, roll back only the touched fields if
  // the server rejects the write.
  const patchCompany = async (companyNormalized: string, fields: CompanyPatchFields) => {
    const previous = companies.find((c) => c.company_normalized === companyNormalized);
    if (!previous) return false;
    const rollback: CompanyPatchFields = Object.fromEntries(
      Object.keys(fields).map((key) => [key, previous[key as keyof CompanyPatchFields]]),
    );
    setCompanies((prev) =>
      prev.map((c) => (c.company_normalized === companyNormalized ? { ...c, ...fields } : c)),
    );
    try {
      const res = await sendJson("/api/contacts/companies", "PATCH", {
        company_normalized: companyNormalized,
        ...fields,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return true;
    } catch (e) {
      setCompanies((prev) =>
        prev.map((c) => (c.company_normalized === companyNormalized ? { ...c, ...rollback } : c)),
      );
      window.alert(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  };

  const commitNotes = (companyNormalized: string) => {
    const draft = (notesDrafts[companyNormalized] || "").trim();
    const current = companies.find((c) => c.company_normalized === companyNormalized);
    if (!current || draft === (current.notes || "")) return;
    patchCompany(companyNormalized, { notes: draft || null });
  };

  return (
    <div>
      <p style={summaryStyle}>
        Companies with at least one contact flagged as a recruiter. Flagging a company as an
        agency or setting its workforce applies to everyone at that company.
      </p>

      {loadError && <div style={{ color: "var(--red)", marginBottom: "12px" }}>{loadError}</div>}

      {!loading && companies.length > 0 && (
        <>
          <div style={toolbar}>
            <input
              type="search"
              placeholder="Search company"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ flex: "1 1 220px", minWidth: "180px" }}
              aria-label="Search companies"
            />
            <button
              type="button"
              onClick={openNextUnclassified}
              disabled={unclassifiedCount === 0}
              title="Opens the company's LinkedIn search in a new tab for you to check yourself -- nothing is scraped automatically"
            >
              Open next unclassified on LinkedIn ({unclassifiedCount})
            </button>
          </div>

          <div style={{ color: "var(--text-muted)", fontSize: "12px", margin: "0 2px 8px" }}>
            {filtered.length.toLocaleString()} of {companies.length.toLocaleString()} companies
          </div>

          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            {filtered.length > 0 && (
              <div style={{ ...rowGrid, ...headerRow }}>
                <span style={headerCell}>Company</span>
                <span style={headerCell}>Contacts</span>
                <span style={headerCell}>Recruiters</span>
                <span style={headerCell}>Agency</span>
                <span style={headerCell}>Workforce</span>
                <span style={headerCell}>Notes</span>
              </div>
            )}
            {filtered.length === 0 ? (
              <div style={{ padding: "18px", color: "var(--text-muted)" }}>
                No companies match this search.
              </div>
            ) : (
              filtered.map((company) => (
                <div
                  key={company.company_normalized}
                  style={
                    company.company_normalized === highlighted
                      ? { ...rowGrid, ...highlightedRow }
                      : rowGrid
                  }
                >
                  <div style={{ fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {company.company_raw}
                  </div>
                  <div>{company.contact_count.toLocaleString()}</div>
                  <div>{company.recruiter_count.toLocaleString()}</div>
                  <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <input
                      type="checkbox"
                      checked={company.is_agency}
                      onChange={(e) =>
                        patchCompany(company.company_normalized, { is_agency: e.target.checked })
                      }
                      aria-label={`${company.company_raw} is a recruiting agency`}
                    />
                    Agency
                  </label>
                  <select
                    value={company.workforce}
                    onChange={(e) =>
                      patchCompany(company.company_normalized, {
                        workforce: e.target.value as CompanyWorkforce,
                      })
                    }
                    aria-label={`Workforce for ${company.company_raw}`}
                    style={{ fontSize: "12px" }}
                  >
                    {WORKFORCE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={notesDrafts[company.company_normalized] ?? ""}
                    onChange={(e) =>
                      setNotesDrafts((prev) => ({
                        ...prev,
                        [company.company_normalized]: e.target.value,
                      }))
                    }
                    onBlur={() => commitNotes(company.company_normalized)}
                    placeholder="Notes"
                    aria-label={`Notes for ${company.company_raw}`}
                    style={{ fontSize: "12px" }}
                  />
                </div>
              ))
            )}
          </div>
        </>
      )}

      {!loading && companies.length === 0 && !loadError && (
        <div style={{ padding: "18px", color: "var(--text-muted)" }} className="card">
          No companies have a recruiter-flagged contact yet. Mark someone as a recruiter in the
          Contacts list and their company will show up here.
        </div>
      )}

      {loading && <div style={{ color: "var(--text-muted)" }}>Loading companies…</div>}
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

const rowGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 90px 100px 90px 150px minmax(120px, 220px)",
  alignItems: "center",
  gap: "12px",
  padding: "10px 14px",
  borderBottom: "1px solid var(--border)",
};

const highlightedRow: CSSProperties = {
  background: "var(--surface2)",
  boxShadow: "inset 3px 0 0 var(--accent, #4a90d9)",
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
