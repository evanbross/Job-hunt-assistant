/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-11
// Read-only display of a stored rich-text note. Kept apart from RichTextEditor.tsx so
// showing notes never pulls in the editor (TipTap/ProseMirror is most of the
// Leads bundle). The server sanitizes every stored note (backend/utils/rich_text.py);
// this sanitizes again before rendering, with the same allowlist.
import { useMemo } from "react";
import { isRichTextEmpty, sanitizeRichText } from "../../utils/richText.ts";

interface RichTextViewProps {
  html: string | null | undefined;
  /** Shown when there is nothing to render. */
  emptyText?: string;
}

export default function RichTextView({ html, emptyText = "" }: RichTextViewProps) {
  const safe = useMemo(() => sanitizeRichText(html || ""), [html]);
  if (isRichTextEmpty(safe)) {
    return emptyText ? <p className="rich-text-empty">{emptyText}</p> : null;
  }
  // Sanitized just above with the stored-notes allowlist.
  return <div className="rich-text" dangerouslySetInnerHTML={{ __html: safe }} />;
}
