/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-28
// "Your profile file changed -- review and load it?" The server works out
// whether a file version is waiting (backend/candidate_profile_file_sync.py)
// every time it's asked, so this asks when the page opens, whenever the server
// signals a change, when the tab regains focus (the usual moment: back from the
// editor), and on a slow timer. Nothing is loaded until the user reviews the
// diff and clicks Load.
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import Modal from "./common/Modal.tsx";
import { useServerEvent } from "../hooks/useServerEvents.ts";
import { apiFetch } from "../utils/apiClient.ts";
import { formatLocalDateTime } from "../utils/dateTimeUtils.ts";
import { describeFetchError } from "../utils/fetchErrorUtils.ts";
import type { ApiErrorBody } from "../types/common.ts";
import type {
  CandidateProfileFileLoadResult,
  CandidateProfileFileReview,
  CandidateProfileFileStatus,
} from "../types/settings.ts";

const STATUS_URL = "/api/candidate-profile/file-sync";
// Focus and pushed signals cover the usual cases; this only bounds a missed one.
const FALLBACK_MS = 5 * 60_000;

async function errorMessage(res: Response): Promise<string> {
  const body: ApiErrorBody | null = await res.json().catch(() => null);
  return body?.error || `Request failed (${res.status})`;
}

function diffLineStyle(line: string): CSSProperties {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
    return { color: "var(--text-muted)" };
  }
  if (line.startsWith("+")) return { background: "var(--green-light)", color: "var(--green)" };
  if (line.startsWith("-")) return { background: "var(--red-light)", color: "var(--red)" };
  return {};
}

function describeLoad(result: CandidateProfileFileLoadResult): string {
  const { rescored, failed } = result.rescore_summary;
  const parts = [`Profile loaded from the file. Re-scored ${rescored} queued job${rescored === 1 ? "" : "s"}`];
  if (failed) parts[0] += ` (${failed} failed)`;
  if (result.backup_path) parts.push(`The replaced profile had changes the file didn't; they were saved to ${result.backup_path}`);
  return parts.join(". ") + ".";
}

export default function CandidateProfileFileBanner() {
  const [status, setStatus] = useState<CandidateProfileFileStatus | null>(null);
  const [review, setReview] = useState<CandidateProfileFileReview | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [busy, setBusy] = useState<"" | "review" | "load" | "dismiss">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshStatus = useCallback(async () => {
    try {
      const res = await apiFetch(STATUS_URL);
      if (!res.ok) return; // keep the last state; the next signal re-reads it
      setStatus(await res.json());
    } catch {
      /* keep the last state; the next signal or fallback re-reads it */
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    const recheck = () => {
      if (!document.hidden) refreshStatus();
    };
    window.addEventListener("focus", recheck);
    document.addEventListener("visibilitychange", recheck);
    return () => {
      window.removeEventListener("focus", recheck);
      document.removeEventListener("visibilitychange", recheck);
    };
  }, [refreshStatus]);
  useServerEvent("candidate-profile-file", refreshStatus, { fallbackMs: FALLBACK_MS });

  const openReview = async () => {
    setReviewOpen(true);
    setBusy("review");
    setError("");
    try {
      const res = await apiFetch(`${STATUS_URL}/review`);
      if (!res.ok) {
        setError(await errorMessage(res));
        return;
      }
      const data: CandidateProfileFileReview = await res.json();
      setReview(data);
      setStatus(data);
    } catch (e) {
      setError(describeFetchError(e));
    } finally {
      setBusy("");
    }
  };

  const closeReview = () => {
    setReviewOpen(false);
    setReview(null);
    setError("");
  };

  const submitAction = async (action: "load" | "dismiss", fileSha256: string) => {
    setBusy(action);
    setError("");
    try {
      const res = await apiFetch(`${STATUS_URL}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_sha256: fileSha256 }),
      });
      if (!res.ok) {
        const message = await errorMessage(res);
        if (res.status === 409) {
          // The file was saved again since it was reviewed: show the new diff.
          await openReview();
        }
        setError(message);
        return;
      }
      if (action === "load") setNotice(describeLoad(await res.json()));
      closeReview();
      await refreshStatus();
    } catch (e) {
      setError(describeFetchError(e));
    } finally {
      setBusy("");
    }
  };

  const offered = status?.state === "reload_available";
  if (!offered && !reviewOpen && !notice) return null;

  return (
    <>
      {notice && (
        <div style={styles.wrap}>
          <div style={{ ...styles.banner, borderColor: "var(--green)", background: "var(--green-light)" }}>
            <span style={styles.text}>{notice}</span>
            <button type="button" className="btn-secondary" style={styles.button} onClick={() => setNotice("")}>
              OK
            </button>
          </div>
        </div>
      )}

      {offered && status && (
        <div style={styles.wrap}>
          <div style={styles.banner} role="status">
            <span style={styles.text}>
              Your profile file was saved {formatLocalDateTime(status.file_modified_at)} and differs from the profile
              the app is using. Fit scoring and cover letters keep using the current one until you load the file.
            </span>
            <div style={{ display: "flex", gap: "8px", flexShrink: 0 }}>
              <button type="button" className="btn-primary" style={styles.button} onClick={openReview}>
                Review changes
              </button>
              <button
                type="button"
                className="btn-secondary"
                style={styles.button}
                disabled={busy !== ""}
                onClick={() => submitAction("dismiss", status.file_sha256)}
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {reviewOpen && (
        <Modal
          title="Load profile file?"
          subtitle={review?.path || status?.path || null}
          onClose={busy === "load" ? undefined : closeReview}
          showCloseButton={busy !== "load"}
          maxWidth="900px"
          footer={
            <>
              {error && <span style={{ color: "var(--red)", fontSize: "13px", marginRight: "auto" }}>{error}</span>}
              <button
                type="button"
                className="btn-secondary"
                disabled={busy !== "" || !review?.file_sha256}
                onClick={() => review && submitAction("dismiss", review.file_sha256)}
              >
                Dismiss this version
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy !== "" || !review?.diff.length}
                onClick={() => review && submitAction("load", review.file_sha256)}
              >
                {busy === "load" ? "Loading and re-scoring…" : "Load file"}
              </button>
            </>
          }
        >
          {busy === "review" && <p style={styles.text}>Comparing the file with the stored profile…</p>}
          {review && review.diff.length === 0 && (
            <p style={styles.text}>The file no longer differs from the stored profile. Nothing to load.</p>
          )}
          {review && review.diff.length > 0 && (
            <>
              <p style={{ ...styles.text, marginTop: 0 }}>
                <span style={diffLineStyle("-")}>Red lines</span> are in the profile the app is using and will be
                replaced; <span style={diffLineStyle("+")}>green lines</span> come from the file. Text that was never
                in the file (such as a learned rule) is saved to a backup file beside it first. Loading re-scores the
                job queue.
              </p>
              <pre style={styles.diff}>
                {review.diff.map((line, index) => (
                  <div key={index} style={diffLineStyle(line)}>{line || " "}</div>
                ))}
              </pre>
            </>
          )}
        </Modal>
      )}
    </>
  );
}

const styles: Record<string, CSSProperties> = {
  wrap: { maxWidth: "1200px", margin: "0 auto", width: "100%", padding: "10px 28px 0 28px" },
  banner: {
    background: "var(--orange-light)",
    border: "1px solid var(--orange)",
    borderRadius: "var(--radius)",
    padding: "10px 18px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "12px",
  },
  text: { color: "var(--text)", fontSize: "13px" },
  button: { fontSize: "12px" },
  diff: {
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
    fontSize: "12px",
    lineHeight: 1.5,
    maxHeight: "55vh",
    overflow: "auto",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    background: "var(--surface2)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "8px",
    margin: 0,
  },
};
