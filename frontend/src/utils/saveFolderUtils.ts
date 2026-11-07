/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-sonnet-5 | 2026-10-07
import type {
  SaveDocumentRequest,
  SaveDocumentResponse,
  SaveDocumentResult,
  SaveStatusDetail,
} from "../types/coverLetter.ts";
import type { SelectFolderResponse } from "../types/system.ts";

// ai: claude-fable-5 | 2026-07-13
/**
 * Opens the backend's native folder picker dialog. Returns the selected
 * absolute path, or null when the user cancels. Throws when the picker
 * itself cannot open (e.g. tkinter unavailable on the server).
 */
export async function pickSaveFolder(initialPath = ""): Promise<string | null> {
  const res = await fetch("/api/system/select-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_path: initialPath }),
  });
  const data: SelectFolderResponse = await res.json();
  if (!res.ok) throw new Error(data.error || "Folder picker failed");
  return data.selected_path || null;
}

// ai: claude-fable-5 | 2026-07-13
/**
 * Recovery flow for a save that failed because the configured folder is
 * unavailable (e.g. it points at a drive that no longer exists). Explains
 * the problem, then opens the native folder picker; if the picker cannot
 * open, falls back to a typed prompt. Returns the replacement folder path,
 * or null when the user cancels.
 */
export async function promptForSaveFolder(unavailableFolder: string): Promise<string | null> {
  window.alert(
    `The configured save folder is not available:\n${unavailableFolder}\n\n` +
      "Choose a new folder in the next dialog.",
  );
  try {
    return await pickSaveFolder("");
  } catch {
    const typed = window.prompt(
      "The folder picker could not open. Enter a folder path to save to instead:",
    );
    return (typed || "").trim() || null;
  }
}

// ai: claude-opus-5-5 | 2026-11-06
/** The file name from a Content-Disposition header (RFC 6266's filename* first). */
export function attachmentFilename(disposition: string | null): string | null {
  if (!disposition || !/^\s*attachment/i.test(disposition)) return null;
  const encoded = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(disposition);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      // fall through to the plain filename
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : null;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ai: claude-opus-5-5 | 2026-11-06 -- was copied into each of the three modals
/**
 * POST /api/cover-letter/save. The desktop app writes the file into a folder and
 * answers with its path; a hosted server sends the file back as an attachment,
 * which is handed to the browser as a download. When the desktop's folder is
 * unavailable, asks for another (promptForSaveFolder) and tries again there.
 * Returns null when the user cancels that; throws on any other failure.
 */
export async function saveDocument(
  body: SaveDocumentRequest,
  folderOverride: string | null = null,
): Promise<SaveDocumentResult | null> {
  const res = await fetch("/api/cover-letter/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(folderOverride ? { ...body, folder: folderOverride } : body),
  });
  const filename = res.ok ? attachmentFilename(res.headers.get("Content-Disposition")) : null;
  if (filename) {
    downloadBlob(await res.blob(), filename);
    return { downloaded: true, filename };
  }
  const data: Partial<SaveDocumentResponse> = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (data.folder_error) {
      const replacement = await promptForSaveFolder(data.folder || "");
      return replacement ? saveDocument(body, replacement) : null;
    }
    throw new Error(data.error || `Save failed (${res.status})`);
  }
  return { downloaded: false, path: data.path || "", folder: data.folder || "" };
}

/** The save status a modal shows for a finished save. */
export function saveStatusFor(result: SaveDocumentResult, format: string): SaveStatusDetail {
  return result.downloaded
    ? { path: result.filename, folder: "", format, downloaded: true }
    : { path: result.path, folder: result.folder, format, downloaded: false };
}
