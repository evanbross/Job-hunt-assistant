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

'use strict';

// ai: claude-sonnet-5 | 2026-09-26
// Shared by every extension context that talks to the listener: loaded with
// importScripts() in the background service worker and with a <script> tag in
// popup.html and options.html. The listener authenticates each request with a
// per-user token (created in the app under Settings > Browser Extension).

const JHA_AUTH_ERROR_MESSAGE =
  'Your extension token is missing or was rejected. Create a new one in Job Hunt App ' +
  '(Settings > Browser Extension) and paste it into the extension options.';

function jhaAuthHeaders(token, extraHeaders) {
  const headers = { ...(extraHeaders || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

// Track whether the listener is currently rejecting our token so the toolbar
// badge shows a persistent "!" instead of failing silently in the background.
let _jhaAuthRejected = null;

function jhaReportAuth(response) {
  let rejected;
  if (response.status === 401) rejected = true;
  else if (response.ok) rejected = false;
  else return; // some other failure says nothing about the token
  if (rejected === _jhaAuthRejected) return;
  _jhaAuthRejected = rejected;
  try {
    chrome.action.setBadgeText({ text: rejected ? '!' : '' });
    if (rejected) chrome.action.setBadgeBackgroundColor({ color: '#cc2f2f' });
  } catch {
    /* the badge is best-effort */
  }
}
