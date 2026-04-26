import React, { useCallback, useEffect, useRef, useState } from "react";
import JobCard from "./JobCard.jsx";

export default function JobQueue({ onTriageCountChange, isAuthenticated, onNavigateToEmails }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanState, setScanState] = useState(null);
  const [rescanAllRunning, setRescanAllRunning] = useState(false);
  const [boardSearching, setBoardSearching] = useState(false);
  const [boardState, setBoardState] = useState(null);
  const [reEvaluating, setReEvaluating] = useState(false);
  const [reEvaluateSummary, setReEvaluateSummary] = useState(null);
  const [manualListingText, setManualListingText] = useState("");
  const [manualSourceUrl, setManualSourceUrl] = useState("");
  const [manualSourceName, setManualSourceName] = useState("");
  const [manualCompanyOverride, setManualCompanyOverride] = useState("");
  const [manualJobTitleOverride, setManualJobTitleOverride] = useState("");
  const [manualSubmitting, setManualSubmitting] = useState(false);
  const [manualResult, setManualResult] = useState(null);
  const [manualExpanded, setManualExpanded] = useState(false);
  const [importContent, setImportContent] = useState("");
  const [importFormat, setImportFormat] = useState("html");
  const [importRecaptureIntervalDays, setImportRecaptureIntervalDays] = useState("4");
  const [importSubmitting, setImportSubmitting] = useState(false);
  const [importSummary, setImportSummary] = useState(null);
  const [importExpanded, setImportExpanded] = useState(false);
  const [recaptureDueSites, setRecaptureDueSites] = useState([]);
  const pollRef = useRef(null);
  const boardPollRef = useRef(null);
  const fileInputRef = useRef(null);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs");
      const data = await res.json();
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      /* no-op */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRecaptureDueSites = useCallback(async () => {
    try {
      const res = await fetch("/api/career-sites/recapture-due");
      const data = await res.json();
      setRecaptureDueSites(Array.isArray(data) ? data : []);
    } catch {
      /* no-op */
    }
  }, []);

  useEffect(() => {
    fetchJobs();
    fetchRecaptureDueSites();
    return () => {
      clearInterval(pollRef.current);
      clearInterval(boardPollRef.current);
    };
  }, [fetchJobs, fetchRecaptureDueSites]);

  const pollScanStatus = async (onDone) => {
    try {
      const res = await fetch("/api/scan/status");
      const state = await res.json();
      setScanState(state);

      if (!state.running) {
        clearInterval(pollRef.current);
        onDone?.();
        await fetchJobs();
        try {
          const t = await fetch("/api/jobs/triage");
          const td = await t.json();
          onTriageCountChange(Array.isArray(td) ? td.length : 0);
        } catch {
          /* no-op */
        }
      }
    } catch {
      clearInterval(pollRef.current);
      onDone?.();
    }
  };

  const startScan = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first (top banner).");
      return;
    }

    const res = await fetch("/api/scan", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start scan");
      return;
    }

    setScanning(true);
    pollRef.current = setInterval(() => pollScanStatus(() => setScanning(false)), 1200);
  };

  const handleRescanAll = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first (top banner).");
      return;
    }
    if (
      !window.confirm(
        "This will delete the history of your applications and skipped jobs, then re-scan every email from scratch.\n\nAre you sure?"
      )
    ) {
      return;
    }

    const res = await fetch("/api/emails/rescan-all", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start rescan");
      return;
    }

    setRescanAllRunning(true);
    setScanState(null);
    pollRef.current = setInterval(
      () => pollScanStatus(() => setRescanAllRunning(false)),
      1200
    );
  };

  const handleAddToSheet = async (jobId) => {
    const res = await fetch(`/api/jobs/${jobId}/add-to-sheet`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to add to sheet");
      return;
    }
    setJobs((prev) => prev.filter((j) => j.id !== jobId));
  };

  const handleSkip = async (jobId, reason = "other", feedback = "", mismatchReasons = []) => {
    const res = await fetch(`/api/jobs/${jobId}/skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, feedback, mismatch_reasons: mismatchReasons }),
    });
    if (res.ok) {
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    }
  };

  const updateTriageCount = async () => {
    try {
      const t = await fetch("/api/jobs/triage");
      const td = await t.json();
      onTriageCountChange(Array.isArray(td) ? td.length : 0);
    } catch {
      /* no-op */
    }
  };

  const handleReEvaluate = async () => {
    if (!isAuthenticated) {
      alert("Connect your Google account first (top banner).");
      return;
    }

    setReEvaluating(true);
    setReEvaluateSummary(null);
    const res = await fetch("/api/jobs/re-evaluate", { method: "POST" });
    const data = await res.json();
    setReEvaluating(false);
    if (!res.ok) {
      alert(data.error || "Failed to re-evaluate jobs");
      return;
    }
    setReEvaluateSummary(data);
    await fetchJobs();
    await updateTriageCount();
  };

  const startBoardSearch = async () => {
    const res = await fetch("/api/board-search", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Failed to start board search");
      return;
    }
    setBoardSearching(true);
    boardPollRef.current = setInterval(pollBoardStatus, 1500);
  };

  const pollBoardStatus = async () => {
    try {
      const res = await fetch("/api/board-search/status");
      const state = await res.json();
      setBoardState(state);
      if (!state.running) {
        clearInterval(boardPollRef.current);
        setBoardSearching(false);
        await fetchJobs();
        try {
          const t = await fetch("/api/jobs/triage");
          const td = await t.json();
          onTriageCountChange(Array.isArray(td) ? td.length : 0);
        } catch { /* no-op */ }
      }
    } catch {
      clearInterval(boardPollRef.current);
      setBoardSearching(false);
    }
  };

  const anyScanRunning = scanning || rescanAllRunning;
  const anyQueueActionRunning = anyScanRunning || boardSearching || reEvaluating || manualSubmitting || importSubmitting;

  const handleManualSubmit = async () => {
    if (!manualListingText.trim() && !manualSourceUrl.trim()) {
      window.alert("Paste a job listing or provide a public job URL first.");
      return;
    }

    setManualSubmitting(true);
    setManualResult(null);
    try {
      const res = await fetch("/api/jobs/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          listing_text: manualListingText,
          source_url: manualSourceUrl,
          source_name: manualSourceName,
          company_override: manualCompanyOverride,
          job_title_override: manualJobTitleOverride,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        window.alert(data.error || "Failed to evaluate pasted job");
        return;
      }

      setManualResult(data.job);
      setManualListingText("");
      setManualSourceUrl("");
      setManualSourceName("");
      setManualCompanyOverride("");
      setManualJobTitleOverride("");
      await fetchJobs();
      await updateTriageCount();
    } catch {
      window.alert("Failed to evaluate pasted job");
    } finally {
      setManualSubmitting(false);
    }
  };

  const handlePasteClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        window.alert("Clipboard is empty.");
        return;
      }
      setImportContent(text);
      setImportFormat("html");
    } catch {
      window.alert("Clipboard read failed. Your browser may require permission.");
    }
  };

  const handleImportFileChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      setImportContent(text);
      setImportFormat(file.name.toLowerCase().endsWith(".mhtml") || file.name.toLowerCase().endsWith(".mht") ? "mhtml" : "html");
    } catch {
      window.alert("Failed to read file.");
    } finally {
      event.target.value = "";
    }
  };

  const buildImportPayload = (sourceFields = {}) => ({
    content: importContent,
    format: importFormat,
    recapture_interval_days: parseInt(importRecaptureIntervalDays, 10),
    ...sourceFields,
  });

  const sourceFieldsFromSuggestion = (suggestion) => {
    if (suggestion.id) {
      return { capture_source_id: suggestion.id };
    }
    return {
      capture_source_name: suggestion.name || "",
      capture_source_url: suggestion.url || "",
    };
  };

  const promptForSourceFields = async (errorData) => {
    const suggestions = Array.isArray(errorData.suggestions) ? errorData.suggestions : [];
    if (errorData.source_match_required && suggestions.length > 0) {
      const suggestion = suggestions[0];
      const label = suggestion.url || suggestion.name || "this site";
      if (window.confirm(`Do you mean ${label}?`)) {
        return sourceFieldsFromSuggestion(suggestion);
      }
    }

    const sourceText = window.prompt("What source site did you capture this from? Enter a known site name or full URL.");
    if (!sourceText?.trim()) {
      return null;
    }
    if (/^https?:\/\//i.test(sourceText.trim())) {
      return { capture_source_url: sourceText.trim() };
    }
    return { capture_source_query: sourceText.trim() };
  };

  const submitImportPayload = async (sourceFields = {}) => {
    const res = await fetch("/api/import/queue-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildImportPayload(sourceFields)),
    });
    const data = await res.json();
    return { res, data };
  };

  const handleExtractAndQueueAll = async () => {
    if (!importContent.trim()) {
      window.alert("Paste HTML or upload an HTML/MHTML file first.");
      return;
    }
    const intervalDays = parseInt(importRecaptureIntervalDays, 10);
    if (!Number.isFinite(intervalDays) || intervalDays < 1) {
      window.alert("Reminder interval must be a positive number of days.");
      return;
    }
    setImportSubmitting(true);
    setImportSummary(null);
    try {
      let { res, data } = await submitImportPayload();
      while (!res.ok && (data.source_required || data.source_match_required)) {
        const sourceFields = await promptForSourceFields(data);
        if (!sourceFields) {
          return;
        }
        ({ res, data } = await submitImportPayload(sourceFields));
      }
      if (!res.ok) {
        window.alert(data.error || "Failed to import jobs");
        return;
      }
      setImportSummary(data);
      await fetchJobs();
      await updateTriageCount();
      await fetchRecaptureDueSites();
    } catch {
      window.alert("Failed to import jobs");
    } finally {
      setImportSubmitting(false);
    }
  };

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "18px", flexWrap: "wrap" }}>
        <button
          className="btn-primary"
          onClick={startScan}
          disabled={anyQueueActionRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {scanning ? "Scanning…" : "Scan Emails"}
        </button>

        <button
          className="btn-secondary"
          onClick={handleRescanAll}
          disabled={anyQueueActionRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {rescanAllRunning ? "Purging…" : "Purge Queue"}
        </button>

        <button
          className="btn-secondary"
          onClick={startBoardSearch}
          disabled={anyQueueActionRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {boardSearching ? "Searching Boards…" : "Search Job Boards"}
        </button>

        <button
          className="btn-secondary"
          onClick={handleReEvaluate}
          disabled={anyQueueActionRunning}
          style={{ padding: "8px 20px", fontSize: "14px" }}
        >
          {reEvaluating ? "Re-evaluating…" : "Re-evaluate"}
        </button>

        {jobs.length > 0 && (
          <span style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            {jobs.length} job{jobs.length !== 1 ? "s" : ""} ready to review
          </span>
        )}
      </div>

      {recaptureDueSites.length > 0 && (
        <div style={{ ...progressBox, background: "rgba(255,193,7,0.08)", borderColor: "rgba(255,193,7,0.28)" }}>
          <p style={{ margin: 0, fontSize: "13px", color: "var(--text)" }}>
            Time to recapture {recaptureDueSites.length} job board{recaptureDueSites.length === 1 ? "" : "s"}:
            {" "}
            {recaptureDueSites.map((site) => site.name || site.url).join(", ")}
          </p>
        </div>
      )}

      <div style={manualBox}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginBottom: manualExpanded ? "12px" : 0 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "15px", color: "var(--text)" }}>Paste Job Listing</h3>
            <p style={{ margin: "4px 0 0 0", fontSize: "12px", color: "var(--text-muted)" }}>
              Paste a public job description or provide a public job URL. If parsing misses fields, fill them in below.
            </p>
          </div>
          <button
            className="btn-secondary"
            onClick={() => setManualExpanded((prev) => !prev)}
            style={{ padding: "8px 18px", fontSize: "14px", alignSelf: "flex-start" }}
          >
            {manualExpanded ? "Collapse" : "Expand"}
          </button>
        </div>

        {manualExpanded && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "10px", marginBottom: "10px" }}>
              <input
                type="text"
                value={manualCompanyOverride}
                onChange={(e) => setManualCompanyOverride(e.target.value)}
                placeholder="Optional company fallback"
                style={inputStyle}
              />
              <input
                type="text"
                value={manualJobTitleOverride}
                onChange={(e) => setManualJobTitleOverride(e.target.value)}
                placeholder="Optional job title fallback"
                style={inputStyle}
              />
              <input
                type="text"
                value={manualSourceName}
                onChange={(e) => setManualSourceName(e.target.value)}
                placeholder="Optional source name"
                style={inputStyle}
              />
              <input
                type="url"
                value={manualSourceUrl}
                onChange={(e) => setManualSourceUrl(e.target.value)}
                placeholder="Optional job URL"
                style={inputStyle}
              />
            </div>

            <textarea
              value={manualListingText}
              onChange={(e) => setManualListingText(e.target.value)}
              placeholder="Paste the full job listing text here..."
              style={textareaStyle}
            />

            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "10px" }}>
              <button
                className="btn-primary"
                onClick={handleManualSubmit}
                disabled={anyQueueActionRunning}
                style={{ padding: "8px 18px", fontSize: "14px" }}
              >
                {manualSubmitting ? "Evaluating…" : "Evaluate & Queue"}
              </button>
            </div>

            {manualResult && (
              <p style={{ marginTop: "10px", fontSize: "12px", color: "var(--green)" }}>
                Queued {manualResult.job_title || "job"} at {manualResult.company || "unknown company"} as{" "}
                <strong>{manualResult.status}</strong>
                {manualResult.fit_score != null ? ` with fit score ${manualResult.fit_score}` : ""}.
              </p>
            )}
          </>
        )}
      </div>

      <div style={manualBox}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginBottom: importExpanded ? "12px" : 0 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "15px", color: "var(--text)" }}>Import Captured HTML</h3>
            <p style={{ margin: "4px 0 0 0", fontSize: "12px", color: "var(--text-muted)" }}>
              Paste captured page HTML or upload `.html` / `.mhtml`. All recognized job links will be categorized and merged into the queue automatically.
            </p>
          </div>
          <button
            className="btn-secondary"
            onClick={() => setImportExpanded((prev) => !prev)}
            style={{ padding: "8px 18px", fontSize: "14px", alignSelf: "flex-start" }}
          >
            {importExpanded ? "Collapse" : "Expand"}
          </button>
        </div>

        {importExpanded && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".html,.htm,.mhtml,.mht,text/html,message/rfc822,multipart/related"
              onChange={handleImportFileChange}
              style={{ display: "none" }}
            />

            <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "10px", flexWrap: "wrap" }}>
              <label style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                Format
              </label>
              <select value={importFormat} onChange={(e) => setImportFormat(e.target.value)} style={selectStyle}>
                <option value="html">HTML</option>
                <option value="mhtml">MHTML</option>
              </select>
              <label style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                Remind every
              </label>
              <input
                type="number"
                min="1"
                value={importRecaptureIntervalDays}
                onChange={(e) => setImportRecaptureIntervalDays(e.target.value)}
                style={{ ...inputStyle, width: "80px" }}
              />
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>days</span>
            </div>

            <textarea
              value={importContent}
              onChange={(e) => setImportContent(e.target.value)}
              placeholder="Paste captured HTML here, or use Paste Clipboard / Upload HTML/MHTML."
              style={textareaStyle}
            />

            <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", flexWrap: "wrap", marginTop: "10px" }}>
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <button
                  className="btn-secondary"
                  onClick={handlePasteClipboard}
                  disabled={importSubmitting}
                  style={{ padding: "8px 16px", fontSize: "13px" }}
                >
                  Paste Clipboard
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={importSubmitting}
                  style={{ padding: "8px 16px", fontSize: "13px" }}
                >
                  Upload HTML/MHTML
                </button>
              </div>
              <button
                className="btn-primary"
                onClick={handleExtractAndQueueAll}
                disabled={importSubmitting}
                style={{ padding: "8px 16px", fontSize: "13px" }}
              >
                {importSubmitting ? "Importing…" : "Extract & Queue All"}
              </button>
            </div>

            {importSummary && (
              <p style={{ marginTop: "10px", fontSize: "12px", color: "var(--green)" }}>
                {importSummary.queued} job{importSummary.queued !== 1 ? "s" : ""} queued
                {importSummary.skipped > 0 ? `, ${importSummary.skipped} already in queue (skipped)` : ""}
                {importSummary.failed > 0 ? `, ${importSummary.failed} failed to fetch` : ""}
                {importSummary.capture_source?.name ? ` from ${importSummary.capture_source.name}` : ""}.
              </p>
            )}
          </>
        )}
      </div>

      {/* Scan / rescan-all progress */}
      {anyScanRunning && scanState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{scanState.message}</span>
            <span>{scanState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${scanState.progress}%` }} />
          </div>
          <ErrorList errors={scanState.errors} onNavigateToEmails={onNavigateToEmails} />
        </div>
      )}

      {/* Completed scan summary */}
      {!anyScanRunning && scanState && scanState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{scanState.message}</p>
          <ErrorList errors={scanState.errors} onNavigateToEmails={onNavigateToEmails} />
        </div>
      )}

      {reEvaluateSummary && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>
            Re-evaluated {reEvaluateSummary.evaluated ?? 0} active job{reEvaluateSummary.evaluated === 1 ? "" : "s"}.
            {" "}{reEvaluateSummary.rescored ?? 0} rescored
            {reEvaluateSummary.sheet_matches ? ` · ${reEvaluateSummary.sheet_matches} sheet match${reEvaluateSummary.sheet_matches === 1 ? "" : "es"}` : ""}
            {reEvaluateSummary.status_changed ? ` · ${reEvaluateSummary.status_changed} moved` : ""}
            {reEvaluateSummary.failed ? ` · ${reEvaluateSummary.failed} failed` : ""}
          </p>
        </div>
      )}

      {/* Board search progress */}
      {boardSearching && boardState && (
        <div style={progressBox}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12px", color: "var(--text-muted)" }}>
            <span>{boardState.message}</span>
            <span>{boardState.progress}%</span>
          </div>
          <div className="loading-bar">
            <div className="loading-bar-fill" style={{ width: `${boardState.progress}%` }} />
          </div>
          {boardState.candidates_found > 0 && (
            <p style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted)" }}>
              {boardState.candidates_processed}/{boardState.candidates_found} scored
              {boardState.jobs_added > 0 && ` · ${boardState.jobs_added} added`}
            </p>
          )}
          {boardState.errors?.length > 0 && (
            <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
              {boardState.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* Completed board search summary */}
      {!boardSearching && boardState && boardState.progress === 100 && (
        <div style={{ ...progressBox, background: "rgba(76,175,129,0.08)", borderColor: "rgba(76,175,129,0.25)", marginBottom: "16px" }}>
          <p style={{ fontSize: "13px", color: "var(--green)" }}>{boardState.message}</p>
        </div>
      )}

      {/* Job cards */}
      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading jobs…</p>
      ) : jobs.length === 0 ? (
        <div className="empty-state">
          <p style={{ fontSize: "32px" }}>🎯</p>
          <p>No jobs queued. Click "Scan Emails" to check your inbox.</p>
        </div>
      ) : (
        jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            onAddToSheet={handleAddToSheet}
            onSkip={handleSkip}
          />
        ))
      )}
    </div>
  );
}

function ErrorList({ errors, onNavigateToEmails }) {
  if (!errors?.length) return null;
  return (
    <ul style={{ marginTop: "8px", fontSize: "11px", color: "var(--red)", paddingLeft: "16px" }}>
      {errors.map((e, i) => {
        const message = typeof e === "string" ? e : e.message;
        const emailId = typeof e === "object" ? e.email_id : null;
        return (
          <li key={i} style={{ marginBottom: "3px" }}>
            {message}
            {emailId && onNavigateToEmails && (
              <button
                onClick={() => onNavigateToEmails(emailId)}
                style={{
                  marginLeft: "8px",
                  fontSize: "10px",
                  padding: "1px 7px",
                  borderRadius: "4px",
                  border: "1px solid var(--red)",
                  background: "transparent",
                  color: "var(--red)",
                  cursor: "pointer",
                  verticalAlign: "middle",
                }}
              >
                View email →
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const progressBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "16px 18px",
  marginBottom: "16px",
  boxShadow: "var(--shadow-sm)",
};

const manualBox = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "16px 18px",
  marginBottom: "16px",
  boxShadow: "var(--shadow-sm)",
};

const inputStyle = {
  width: "100%",
  minWidth: 0,
  padding: "10px 12px",
  borderRadius: "var(--radius)",
  border: "1px solid var(--border)",
  background: "var(--bg-elevated)",
  color: "var(--text)",
};

const selectStyle = {
  padding: "8px 10px",
  borderRadius: "var(--radius)",
  border: "1px solid var(--border)",
  background: "var(--bg-elevated)",
  color: "var(--text)",
};

const textareaStyle = {
  width: "100%",
  minHeight: "180px",
  resize: "vertical",
  padding: "12px",
  borderRadius: "var(--radius)",
  border: "1px solid var(--border)",
  background: "var(--bg-elevated)",
  color: "var(--text)",
  fontFamily: "inherit",
  fontSize: "13px",
  lineHeight: 1.5,
};
