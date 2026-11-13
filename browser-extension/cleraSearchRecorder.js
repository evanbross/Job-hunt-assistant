/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-02
// Runs in the page's own JS world (manifest "world": "MAIN") on getclera.com,
// from document_start, and only watches: it records the filters of the most
// recent job search the page itself sends (POST /api/talents/me/external-jobs)
// on <html data-jha-clera-search>, where content.js (isolated world, which
// can't see the page's requests) reads it. content.js then pages through
// exactly the search on screen -- the request body can't be rebuilt from the
// page URL (e.g. ?experience=senior is sent as ["5-10","10+"]). Every call
// passes straight through to the real fetch; nothing here alters, delays, or
// blocks a request.
(() => {
  const ATTRIBUTE = 'data-jha-clera-search';
  const SEARCH_PATH = '/api/talents/me/external-jobs';

  function record(input, init) {
    try {
      if (String(init?.method || '').toUpperCase() !== 'POST' || typeof init.body !== 'string') return;
      if (new URL(String(input), location.href).pathname !== SEARCH_PATH) return;
      const { cursor: _cursor, ...search } = JSON.parse(init.body);
      if (typeof search.filters !== 'object' || search.filters === null) return;
      // Without the cursor, the replay starts from the first page however far
      // the user had clicked "Load more".
      document.documentElement.setAttribute(ATTRIBUTE, JSON.stringify(search));
    } catch {
      // not a JSON search request -- nothing to record
    }
  }

  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    if (typeof input === 'string' || input instanceof URL) record(input, init);
    return realFetch.apply(this, arguments);
  };
})();
