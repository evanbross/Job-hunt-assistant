/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-29
// Runs in the page's own JS world (manifest "world": "MAIN") on careers
// search-results pages, from document_start, and only watches: it records the
// most recent Phenom People search request the page itself sends -- body and
// CSRF token -- on <html data-jha-phenom-search>, where content.js (isolated
// world, which can't see the page's requests) reads it. content.js then pages
// through exactly the search on screen, including what it can't rebuild from
// the filter checkboxes: the location box, its radius, a token the page has
// since refreshed. Every call passes straight through to the real fetch/XHR;
// nothing here alters, delays, or blocks a request.
(() => {
  const ATTRIBUTE = 'data-jha-phenom-search';

  function record(url, body, csrfToken) {
    try {
      if (typeof body !== 'string' || !/\/widgets(?:[?#]|$)/.test(String(url))) return;
      const parsed = JSON.parse(body);
      if (!/RefineSearch$/i.test(parsed?.ddoKey || '') || typeof parsed.selected_fields !== 'object') return;
      document.documentElement.setAttribute(ATTRIBUTE, JSON.stringify({ body: parsed, csrfToken: csrfToken || '' }));
    } catch {
      // not a JSON search request -- nothing to record
    }
  }

  function csrfFromInit(headers) {
    if (!headers) return '';
    if (typeof headers.get === 'function') return headers.get('X-CSRF-TOKEN') || '';
    const entries = Array.isArray(headers) ? headers : Object.entries(headers);
    const found = entries.find(([name]) => String(name).toLowerCase() === 'x-csrf-token');
    return found ? String(found[1]) : '';
  }

  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    if (typeof input === 'string' || input instanceof URL) {
      record(input, init?.body, csrfFromInit(init?.headers));
    }
    return realFetch.apply(this, arguments);
  };

  const realOpen = XMLHttpRequest.prototype.open;
  const realSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const realSend = XMLHttpRequest.prototype.send;
  const requests = new WeakMap();
  XMLHttpRequest.prototype.open = function (method, url) {
    requests.set(this, { url, csrfToken: '' });
    return realOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    const request = requests.get(this);
    if (request && String(name).toLowerCase() === 'x-csrf-token') request.csrfToken = String(value);
    return realSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const request = requests.get(this);
    if (request) record(request.url, body, request.csrfToken);
    return realSend.apply(this, arguments);
  };
})();
