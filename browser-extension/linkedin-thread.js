/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// Not 'use strict': the popup tests load it with window.eval, where a strict
// script's top-level function would stay local instead of becoming a global.
// Shared by the popup (a <script> tag in popup.html), which offers "Capture this
// conversation" on a LinkedIn thread, and background.js (importScripts), which
// captures it. A thread's URL is linkedin.com/messaging/thread/<id>/, and <id>
// is LinkedIn's conversation id -- the same one its messaging data and the
// messages.csv export use.
const JHA_LINKEDIN_THREAD_RE = /^https:\/\/(www\.)?linkedin\.com\/messaging\/thread\/([^/?#]+)/i;

function jhaLinkedInThreadId(url) {
  const match = JHA_LINKEDIN_THREAD_RE.exec(url || '');
  if (!match) return null;
  try {
    return decodeURIComponent(match[2]);
  } catch {
    return null;
  }
}
