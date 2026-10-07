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
