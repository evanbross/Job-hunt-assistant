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

// Guard: only register listeners once per tab, even if executeScript is called again.
(function () {
  'use strict';

  if (window.__jhaContentLoaded) return;
  window.__jhaContentLoaded = true;

  // ─── Page Detection ────────────────────────────────────────────────────────

  function detectPage() {
    const host = location.hostname;
    const path = location.pathname;

    if (host.includes('linkedin.com')) {
      if (/\/jobs\/view\/\d+/.test(path)) {
        return { site: 'LinkedIn', type: 'linkedInJob', jobCount: 1, description: 'Single job listing' };
      }
      if (/\/jobs\/(search|collections|search-results)/.test(path)) {
        const count = countLinkedInJobs();
        // ai: claude-sonnet-5 | 2026-08-25
        // Unlike Insight Global's split view, LinkedIn's search page puts the
        // selected job's id right in the URL (?currentJobId=...), updated on
        // every card click — no extra tracking needed to know which job is
        // open. See buildLinkedInSearchDetailPayload.
        return {
          site: 'LinkedIn Search',
          type: 'linkedInSearch',
          jobCount: count,
          description: `${count} jobs visible`,
          captureModes: ['list', 'detail'],
        };
      }
    }

    if (host.includes('wellfound.com')) {
      if (/^\/jobs\/\d+/.test(path)) {
        return { site: 'Wellfound', type: 'wellfoundJob', jobCount: 1, description: 'Single job listing' };
      }
      if (/^\/jobs/.test(path)) {
        const slugs = collectWellfoundSlugs();
        const count = slugs.length;
        return {
          site: 'Wellfound Jobs',
          type: 'wellfoundSearch',
          jobCount: count,
          description: `${count} jobs — fetches details (~${Math.ceil(count * 0.5)}s)`,
        };
      }
    }

    // ai: claude-sonnet-5 | 2026-08-11
    // Individual Greenhouse job pages appear on three host shapes: my.greenhouse.io
    // (embed/apply flow — <slug>/jobs/<numeric-id> or jobs/<slug>/<numeric-id>, both
    // observed in the wild) and the two public board hosts job-boards.greenhouse.io /
    // boards.greenhouse.io (<slug>/jobs/<numeric-id> only). Checked first because the
    // search-page check below would otherwise also match.
    const _GH_PUBLIC_BOARD_HOSTS = new Set(['job-boards.greenhouse.io', 'boards.greenhouse.io']);
    const isGreenhouseHost = host === 'my.greenhouse.io' || _GH_PUBLIC_BOARD_HOSTS.has(host);
    if (
      isGreenhouseHost &&
      (/^\/[^/]+\/jobs\/\d+/.test(path) || /^\/jobs\/[^/]+\/\d+/.test(path))
    ) {
      return { site: 'Greenhouse', type: 'greenhouseJob', jobCount: 1, description: 'Single job listing' };
    }

    // my.greenhouse.io search pages live at /jobs; the public board hosts' board/search
    // pages are just /<company-slug> (optionally with query filters like
    // ?departments[]=...&keyword=...) since the single-job check above already
    // consumed the /<company-slug>/jobs/<id> shape.
    const isGreenhouseSearchPath = host === 'my.greenhouse.io'
      ? /^\/jobs/.test(path)
      : _GH_PUBLIC_BOARD_HOSTS.has(host) && /^\/[^/]+/.test(path);
    if (isGreenhouseSearchPath) {
      // ai: claude-sonnet-4-6 | 2026-06-25
      // CSS attribute selectors match the raw href value, not the resolved URL, so
      // relative hrefs like /jobs/12345 would miss the greenhouse.io check. Use a.href
      // (always the resolved absolute URL) via JS iteration instead.
      const count = [...document.querySelectorAll('a[href]')].filter(a => {
        try {
          const u = new URL(a.href);
          return u.hostname.includes('greenhouse.io') && /\/jobs\/[\w-]{3,}/.test(u.pathname);
        } catch { return false; }
      }).length;
      return { site: 'Greenhouse', type: 'greenhouseSearch', jobCount: count, description: `${count}+ jobs (auto-scrolls to load all)` };
    }

    // ai: claude-sonnet-5 | 2026-07-23
    if (host.includes('jobgether.com') && /^\/offer\//.test(path)) {
      return { site: 'Jobgether', type: 'jobgetherJob', jobCount: 1, description: 'Single job listing' };
    }

    // ai: claude-sonnet-4-6 | 2026-06-20
    if (host.includes('jobanni.com') && /^\/search/.test(path)) {
      return { site: 'Jobanni', type: 'jobanni', jobCount: 0, description: 'Fetches job data via RSC API (~3s)' };
    }

    if (host === 'hnhiring.com') {
      let count = 0;
      document.querySelectorAll('a[href]').forEach(a => {
        if (a.href.startsWith('http') && !a.href.includes('hnhiring.com') && !a.href.includes('ycombinator.com')) count++;
      });
      return { site: 'HN Hiring', type: 'hnHiring', jobCount: count, description: `${count} job links` };
    }

    if (host.includes('workatastartup.com')) {
      const count = new Set(
        [...document.querySelectorAll('a[href]')]
          .map(a => a.href.match(/workatastartup\.com\/jobs\/(\d+)/)?.[1])
          .filter(Boolean)
      ).size;
      return { site: 'Work at a Startup', type: 'workAtAStartup', jobCount: count, description: `${count} jobs` };
    }

    if (host === 'discord.com' && /^\/careers/.test(path)) {
      const count = new Set(
        [...document.querySelectorAll('a[href]')]
          .map(a => a.getAttribute('href')?.match(/^\/jobs\/(\d+)/)?.[1])
          .filter(Boolean)
      ).size;
      return { site: 'Discord Careers', type: 'discordCareers', jobCount: count, description: `${count} jobs` };
    }

    if (host === 'jobs.ashbyhq.com') {
      const segments = path.split('/').filter(Boolean);
      if (segments.length >= 2) {
        return { site: 'Ashby', type: 'ashbyJob', jobCount: 1, description: 'Single job listing' };
      }
      if (segments.length === 1) {
        const count = getAshbyBoardJobs().length;
        return { site: 'Ashby', type: 'ashbySearch', jobCount: count, description: `${count} jobs` };
      }
    }

    // ai: claude-sonnet-4-6 | 2026-06-20
    if (host.includes('welcometothejungle.com')) {
      // Match canonical /companies/<slug>/jobs/<id> AND email-notification /jobs/<id> links.
      if (/\/companies\/[^/?#]+\/jobs\/[^/?#]{2,}/.test(path) || /^\/jobs\/[^/?#]{2,}/.test(path)) {
        return { site: 'Welcome to the Jungle', type: 'wttjJob', jobCount: 1, description: 'Single job listing' };
      }
    }

    if (host.includes('mongodb.com') && /\/careers/.test(path)) {
      const count = new Set(
        [...document.querySelectorAll('a[href*="gh_jid="]')]
          .map(a => { try { return new URL(a.href).searchParams.get('gh_jid'); } catch { return null; } })
          .filter(Boolean)
      ).size;
      return { site: 'MongoDB Careers', type: 'mongoDBCareer', jobCount: count, description: `${count} jobs` };
    }

    // ai: claude-sonnet-5 | 2026-07-28
    // Insight Global's search-results page is server-rendered with numbered-page
    // URLs (/jobs/find_a_job/2/, /3/, …) rather than an in-page "load more" — the
    // old smartGeneric fallback only ever saw page 1. Detail pages have extra path
    // segments ending in /job-<id>/ and must be checked first so they don't match
    // the bare-search-page pattern below.
    if (host.includes('insightglobal.com')) {
      if (/^\/jobs\/find_a_job\/.+\/job-\d+\/?$/i.test(path)) {
        return { site: 'Insight Global', type: 'insightGlobalJob', jobCount: 1, description: 'Single job listing' };
      }
      if (/^\/jobs\/find_a_job\/(?:\d+\/)?$/.test(path)) {
        const total = insightGlobalTotalHits();
        return {
          site: 'Insight Global',
          type: 'insightGlobalSearch',
          jobCount: total,
          description: `${total} jobs — auto-fetches all pages`,
        };
      }
      // ai: claude-sonnet-5 | 2026-08-24
      // Insight Global's redesigned search UI (as of this writing, replacing
      // the /jobs/find_a_job/ pages checked above, which are kept in case any
      // still-live links resolve there): one URL renders both the result list
      // (left) and the currently-selected job's full detail (right) via
      // Angular Router state that never appears in location.href — clicking a
      // card re-renders the right pane in place with no navigation at all.
      // That means the page can't be classified as "one job" or "many jobs"
      // by URL shape the way every other detectPage() branch does; the user
      // has to say which one they want. See captureModes below and the
      // captureMode branch in buildPayload's dispatcher.
      if (/^\/jobs\/search\//.test(path)) {
        const total = insightGlobalTotalHits();
        return {
          site: 'Insight Global',
          type: 'insightGlobalSplit',
          jobCount: total,
          description: `${total} jobs found`,
          captureModes: ['list', 'detail'],
        };
      }
    }

    // ai: claude-sonnet-5 | 2026-07-28
    // Kforce's search page is a client-side Angular app entirely behind one
    // fixed path (/find-work/search-jobs/) — job identity lives in
    // location.hash (#/detail/<id>/ for a single posting, #/?t=...&l=...
    // for a search), which countSmartGenericJobs() can't see because it
    // only inspects u.pathname. "Load more" appends 25 rows client-side
    // per click rather than paginating via URL or a fetchable API.
    if (host.includes('kforce.com') && /^\/find-work\/search-jobs\/?$/.test(path)) {
      if (/^#\/detail\//.test(location.hash)) {
        return { site: 'Kforce', type: 'kforceJob', jobCount: 1, description: 'Single job listing' };
      }
      const total = kforceTotalHits();
      return {
        site: 'Kforce',
        type: 'kforceSearch',
        jobCount: total,
        description: total ? `${total} jobs — auto-clicks "Load more" to fetch all` : 'No jobs found',
      };
    }

    // ai: claude-sonnet-4-6 | 2026-06-17
    if (isPhenomSearchPage()) {
      const total = phenomTotalHits();
      const company = (document.title.match(/[-–]\s*(.+?)\s+Careers/i) || [])[1] || host;
      return {
        site: company,
        type: 'phenomSearch',
        jobCount: total,
        description: `${total} jobs — auto-fetches all pages`,
      };
    }

    // ai: claude-fable-5 | 2026-07-13
    // Jibe/iCIMS career sites (careers.marriott.com, …): job URLs end in
    // /job/<32-hex-id> and each page embeds a schema.org JobPosting JSON-LD
    // block with the full description and req ID.
    if (_JIBE_JOB_PATH_RE.test(path)) {
      return { site: host, type: 'jibeJob', jobCount: 1, description: 'Single job listing' };
    }
    const jibeJobs = collectJibeJobLinks();
    if (jibeJobs.length >= 2) {
      return {
        site: host,
        type: 'jibeSearch',
        jobCount: jibeJobs.length,
        description: `${jibeJobs.length} jobs — fetches full descriptions (~${Math.ceil(jibeJobs.length * 0.5)}s)`,
      };
    }

    // Generic fallback: scan for job-shaped links before giving up on counting.
    const smartCount = countSmartGenericJobs();
    if (smartCount >= 2) {
      return { site: location.hostname, type: 'smartGeneric', jobCount: smartCount, description: `${smartCount} jobs detected` };
    }
    return { site: location.hostname, type: 'html', jobCount: 0, description: 'Will send page HTML for parsing' };
  }

  function countLinkedInJobs() {
    const ids = new Set();
    document.querySelectorAll('[data-job-id], [data-occludable-job-id]').forEach(el => {
      const id = el.getAttribute('data-job-id') || el.getAttribute('data-occludable-job-id');
      if (id && /^\d{8,}/.test(id)) ids.add(id);
    });
    document.querySelectorAll('a[href*="/view/"]').forEach(a => {
      const m = a.href.match(/\/view\/(\d+)/);
      if (m) ids.add(m[1]);
    });
    return ids.size;
  }

  // Job-URL heuristic: matches paths like /job/123, /jobs/abc, /careers/job/456, etc.
  // Excludes navigation-only pages (search filters, category pages, apply form).
  const _JOB_PATH_RE = /\/jobs?\/[\w%.-]{2,}/i;
  const _JOB_EXCLUDE_RE = /\/(search|filter|categories?|department|location|apply|login|signin|signup|about|contact|faq|privacy|terms|pages?)\b/i; // ai: claude-fable-5 | 2026-07-13 — "pages?" excludes /jobs/page/N pagination links
  // Cross-origin job links are only trusted if they're on a known ATS domain.
  // This prevents email tracker URLs (Sendgrid, Mailchimp, etc.) from being
  // picked up just because the encoded redirect path contains "/job/".
  const _KNOWN_ATS_RE = /\.(greenhouse\.io|lever\.co|myworkdayjobs\.com|ashbyhq\.com|taleo\.net|icims\.com|smartrecruiters\.com|jobvite\.com|workday\.com|jazz\.co|bamboohr\.com|recruitee\.com)$/i;

  function isJobLink(u) {
    if (!_JOB_PATH_RE.test(u.pathname) || _JOB_EXCLUDE_RE.test(u.pathname)) return false;
    // In-page anchors (e.g. "Skip to main content", "Back to top") resolve to
    // the current page's own URL plus a hash — same pathname, just a scroll
    // target, not a distinct job posting.
    if (u.hash && u.pathname === location.pathname && u.search === location.search) return false;
    return u.hostname === location.hostname || _KNOWN_ATS_RE.test(u.hostname);
  }

  function countSmartGenericJobs() {
    const seen = new Set();
    document.querySelectorAll('a[href]').forEach(a => {
      try {
        const u = new URL(a.href);
        if (isJobLink(u)) seen.add(u.origin + u.pathname);
      } catch { /* skip */ }
    });
    // The current page counts as a job too when it is one — see the matching
    // self-inclusion in buildSmartGenericPayload().
    try {
      const selfUrl = new URL(location.href);
      if (isJobLink(selfUrl)) seen.add(selfUrl.origin + selfUrl.pathname);
    } catch { /* skip */ }
    return seen.size;
  }

  function collectWellfoundSlugs() {
    const seen = new Set();
    document.querySelectorAll('a[href*="/jobs/"]').forEach(a => {
      const m = a.href.match(/wellfound\.com\/jobs\/(\d[^?#]*)/);
      if (m && a.innerText.split('\n')[0].trim()) seen.add(m[1].replace(/\/$/, ''));
    });
    return [...seen];
  }

  // ai: claude-sonnet-4-6 | 2026-07-06
  // window.__appData.jobBoard.jobPostings is SSR'd into the initial HTML on the
  // Ashby company job-board page (unlike window.__appData.posting on a single job
  // page, which fills in later), so it's available synchronously here. The board
  // itself is just that full org-wide array client-filtered by URL query params —
  // mirror those filters so the count matches what's actually on screen.
  function getAshbyBoardJobs() {
    const postings = window.__appData?.jobBoard?.jobPostings || [];
    const params = new URLSearchParams(location.search);
    const departmentId = params.get('departmentId');
    const locationId = params.get('locationId');
    const teamId = params.get('teamId');
    const workplaceType = params.get('workplaceType');
    return postings.filter(p => {
      if (!p.isListed) return false;
      if (departmentId && p.departmentId !== departmentId) return false;
      if (locationId && p.locationId !== locationId) return false;
      if (teamId && p.teamId !== teamId) return false;
      if (workplaceType && p.workplaceType !== workplaceType) return false;
      return true;
    });
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ai: claude-sonnet-4-6 | 2026-06-13
  function promptForCompanyName() {
    return window.prompt('Company name could not be detected. Please enter the company name:') || '';
  }

  function pick(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText.trim()) return el.innerText.trim();
    }
    return '';
  }

  function longestText(selectors) {
    let best = '';
    selectors.forEach(sel => {
      document.querySelectorAll(sel).forEach(el => {
        const text = el.innerText?.trim() || '';
        if (text.length > best.length) best = text;
      });
    });
    return best;
  }

  function longestTextFromDoc(doc, selectors) {
    let best = '';
    selectors.forEach(sel => {
      doc.querySelectorAll(sel).forEach(el => {
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (text.length > best.length) best = text;
      });
    });
    return best;
  }

  // Sends a progress update to all extension listeners (popup if open, ignored otherwise).
  function sendProgress(text, progress) {
    chrome.runtime.sendMessage({ action: 'progress', text, progress });
  }

  // ─── Payload Builders ──────────────────────────────────────────────────────

  function buildHtmlPayload() {
    return JSON.stringify({ content: document.documentElement.outerHTML, url: location.href });
  }

  // ai: claude-sonnet-5 | 2026-07-12
  // Wellfound's job-detail panel (and its "taken down" closed banner) is fetched
  // by a second client-side call after the initial listings-grid shell renders —
  // confirmed via captured __NEXT_DATA__ showing the router already recognized
  // job_listing_slug while the DOM still only had the generic grid. A fixed delay
  // can't reliably cover this (worse in a throttled background tab), so poll
  // until the page stops growing instead of guessing a wait time.
  //
  // ai: claude-sonnet-5 | 2026-07-24
  // minWaitMs guards against a real failure mode (see call site): an ordinary
  // ~1s pause between a page's sequential render stages can otherwise look
  // identical to "done" after just 2 unchanged reads (~1.5s in), so a caller
  // whose page genuinely renders in one shot can still pass 0/2 for a fast
  // exit — only Wellfound's multi-stage render needs the higher floor, kept
  // explicit at that call site rather than defaulted here for every caller.
  async function waitForStableRender(
    maxWaitMs,
    checkIntervalMs = 500,
    stableChecks = 2,
    minWaitMs = 0
  ) {
    const deadline = Date.now() + maxWaitMs;
    const earliestExit = Date.now() + minWaitMs;
    let lastLen = -1;
    let stableCount = 0;
    while (Date.now() < deadline) {
      await sleep(checkIntervalMs);
      const len = document.body.innerText.length;
      if (len === lastLen) {
        stableCount++;
        if (stableCount >= stableChecks && Date.now() >= earliestExit) break;
      } else {
        stableCount = 0;
      }
      lastLen = len;
    }
  }

  // ai: claude-sonnet-4-6 | 2026-07-02
  // Closed-req checks need the raw rendered page (banner text and all), not the
  // structured job-listing extraction the site-specific builders produce.
  async function buildClosedReqCheckPayload() {
    if (location.hostname.includes('wellfound.com')) {
      // ai: claude-sonnet-5 | 2026-07-24
      // stableChecks=4 + minWaitMs=3000: Wellfound's job-detail panel (and its
      // "taken down" closed banner) mounts after a second client-side call, in
      // several sequential stages — confirmed via 8 real jobs that stayed
      // misclassified "open" across 6-7 rechecks over 11 days because the
      // default (2 checks, no floor) exited during an ordinary ~1s pause
      // between stages, before the banner text had actually rendered.
      await waitForStableRender(10000, 500, 4, 3000);
    } else if (location.hostname.includes('kforce.com')) {
      // ai: claude-sonnet-5 | 2026-08-23
      // Kforce's job detail (and its "This job is unavailable." closed state —
      // confirmed live against two expired real postings) is rendered
      // client-side by Angular after a single async lookup; capturing
      // immediately on page load would still show the transient "Loading..."
      // placeholder instead of either outcome.
      await waitForStableRender(10000, 500, 3, 2500);
    }
    return JSON.stringify({
      content: document.documentElement.outerHTML,
      url: location.href,
      format: 'html',
    });
  }

  // ai: claude-sonnet-5 | 2026-08-25
  // urlOverride lets buildLinkedInSearchDetailPayload reuse this same
  // extraction against the search page's inline detail pane (confirmed live:
  // the same selectors below match there) while still recording the
  // canonical /jobs/view/<id>/ URL rather than the search page's own URL.
  async function buildLinkedInJobPayload(urlOverride) {
    const url = urlOverride || location.href;
    const seeMoreBtn = document.querySelector(
      'button.show-more-less-html__button--more, ' +
      'button[aria-label*="more description"], ' +
      'button[aria-label*="Click to see more"], ' +
      'button[aria-label*="See more"]'
    );
    if (seeMoreBtn) { seeMoreBtn.click(); await sleep(350); }

    const titleFromPage = pick([
      'h1.top-card-layout__title',
      '.job-details-jobs-unified-top-card__job-title h1',
      '.job-details-jobs-unified-top-card__job-title',
      '.jobs-unified-top-card__job-title h1',
      '.jobs-unified-top-card__job-title',
      '[data-test-job-title]',
      'h1',
    ]);
    const companyFromPage = pick([
      '.top-card-layout__second-subline a',
      '.job-details-jobs-unified-top-card__company-name a',
      '.job-details-jobs-unified-top-card__company-name',
      '.topcard__org-name-link',
      '.jobs-unified-top-card__company-name a',
      '.jobs-unified-top-card__company-name',
      '[data-test-job-company-name]',
    ]);

    const cleanTitle = v => (v || '').replace(/\s*\|\s*LinkedIn\s*$/i, '').replace(/\s+at\s+.+$/i, '').trim();
    const cleanCompany = v => (v || '').replace(/\s*\|\s*LinkedIn\s*$/i, '').trim();

    let titleFallback = { title: '', company: '' };
    const cleanDocTitle = (document.title || '').replace(/\s*\|\s*LinkedIn\s*$/i, '').trim();
    const docMatch = cleanDocTitle.match(/^(.+?)\s+at\s+(.+)$/i);
    if (docMatch) titleFallback = { title: docMatch[1].trim(), company: docMatch[2].trim() };

    const title = cleanTitle(titleFromPage) || titleFallback.title;
    const company = cleanCompany(companyFromPage) || titleFallback.company;
    const description = longestText([
      '.show-more-less-html__markup',
      '.jobs-description-content__text',
      '.jobs-description__content',
      '.jobs-description-content',
      '.jobs-box__html-content',
      '.jobs-description',
      '#job-details',
      '.jobs-search__job-details--container',
      '.scaffold-layout__detail',
      'main',
    ]);

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + url);
    // ai: claude-sonnet-4-6 | 2026-06-26
    // Fall back to full page body text whenever a structured description isn't found,
    // even if title/company were extracted. This ensures queue-captured pages always
    // have enough content for the importer (old guard required ALL THREE to be missing).
    if (description) {
      lines.push('', description);
    } else {
      lines.push('', document.body.innerText.trim().slice(0, 12000));
    }

    return JSON.stringify({ jobs: [{ url, job_title: title, company, listing_text: lines.join('\n') }] });
  }

  // ai: claude-sonnet-5 | 2026-08-25
  // currentJobId is set by LinkedIn itself the moment a job auto-selects or
  // gets clicked (confirmed live: updates in the URL bar on every card
  // click, no navigation), so unlike Insight Global there's never a need to
  // click a card ourselves. But the id lands in the URL before the detail
  // pane's own async fetch finishes rendering it — confirmed live: reading
  // the pane immediately after a fresh page load caught it mid-spinner and
  // returned an empty title/company. Poll briefly for the title selectors
  // used by buildLinkedInJobPayload to return something before extracting.
  const _LI_DETAIL_TITLE_SELECTORS = [
    'h1.top-card-layout__title',
    '.job-details-jobs-unified-top-card__job-title h1',
    '.job-details-jobs-unified-top-card__job-title',
    '.jobs-unified-top-card__job-title h1',
    '.jobs-unified-top-card__job-title',
    '[data-test-job-title]',
  ];

  async function waitForLinkedInDetailRender(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (pick(_LI_DETAIL_TITLE_SELECTORS)) return true;
      await sleep(200);
    }
    return false;
  }

  async function buildLinkedInSearchDetailPayload() {
    const jobId = new URLSearchParams(location.search).get('currentJobId');
    const url = jobId ? `https://www.linkedin.com/jobs/view/${jobId}/` : location.href;
    if (jobId) await waitForLinkedInDetailRender();
    return buildLinkedInJobPayload(url);
  }

  function buildLinkedInSearchPayload() {
    const jobIds = new Set();
    const jobs = [];

    const cardElements = document.querySelectorAll(
      '[data-view-name="job-card"], [data-job-id], [data-occludable-job-id], ' +
      '.job-card-list__container, .job-card-container'
    );

    cardElements.forEach(card => {
      const linkEl = card.querySelector('a[href*="/view/"]');
      const id = card.getAttribute('data-job-id') ||
                 card.getAttribute('data-occludable-job-id') ||
                 linkEl?.href.match(/\/view\/(\d+)/)?.[1];
      if (!id || jobIds.has(id)) return;
      jobIds.add(id);

      const titleEl = card.querySelector(
        '.job-card-list__title, .job-card-container__link, .artdeco-entity-lockup__title, a'
      );
      const companyEl = card.querySelector(
        '.job-card-container__company-name, .artdeco-entity-lockup__subtitle, ' +
        '.job-card-container__primary-description, .job-card-container__company-link'
      );

      const url = `https://www.linkedin.com/jobs/view/${id}/`;
      const title = titleEl ? titleEl.innerText.trim() : 'Unknown Title';
      const company = companyEl ? companyEl.innerText.trim() : '';

      jobs.push({
        url,
        job_title: title,
        company,
        listing_text: `${title}\nCompany: ${company}\nURL: ${url}\n\nSnippet:\n${card.innerText.trim()}`,
      });
    });

    if (jobs.length === 0) {
      document.querySelectorAll('a[href*="/view/"]').forEach(link => {
        const m = link.href.match(/\/view\/(\d+)/);
        if (!m || jobIds.has(m[1])) return;
        jobIds.add(m[1]);
        const url = `https://www.linkedin.com/jobs/view/${m[1]}/`;
        jobs.push({ url, job_title: link.innerText.trim() || 'LinkedIn Job', company: '', listing_text: `URL: ${url}` });
      });
    }

    return JSON.stringify({ jobs });
  }

  // ai: claude-sonnet-5 | 2026-08-11
  const WELLFOUND_REMOTE_KIND_LABELS = {
    ONSITE_OR_REMOTE: 'Onsite or remote',
    FULLY_REMOTE: 'Fully remote',
    REMOTE_FIRST: 'Remote-first',
    NOT_REMOTE: 'Not remote',
  };

  // ai: claude-sonnet-5 | 2026-08-11
  function wellfoundListingIdFromSlug(slug) {
    const m = (slug || '').match(/^(\d+)/);
    return m ? m[1] : '';
  }

  // ai: claude-sonnet-5 | 2026-08-11
  // Salary, location, and remote-work-policy render in a page sidebar that none of
  // the description selectors reach, so a description-only capture silently omits
  // them — the fit-scoring AI then reads the listing as "no salary stated" even when
  // it's shown right on the page. These fields don't have stable DOM selectors either,
  // but Wellfound embeds them as structured data in the page's Next.js/Apollo cache.
  function extractWellfoundListingMetadata(doc, listingId) {
    if (!listingId) return '';
    try {
      const nextDataEl = doc.getElementById('__NEXT_DATA__');
      if (!nextDataEl) return '';
      const nd = JSON.parse(nextDataEl.textContent || '{}');
      const data = nd?.props?.pageProps?.apolloState?.data;
      const jl = data?.['JobListing:' + listingId];
      if (!jl) return '';
      const cfgRef = jl.remoteConfig?.__ref;
      const cfgKind = cfgRef ? data?.[cfgRef]?.kind : null;

      const lines = [];
      if (jl.compensation) lines.push('Compensation: ' + jl.compensation);
      if (jl.locationNames?.length) lines.push('Location: ' + jl.locationNames.join(', '));
      lines.push(
        'Remote work policy: ' +
          (WELLFOUND_REMOTE_KIND_LABELS[cfgKind] || (jl.remote ? 'Remote' : 'Not remote'))
      );
      if (jl.acceptedRemoteLocationNames?.length) {
        lines.push('Hires remotely in: ' + jl.acceptedRemoteLocationNames.join(', '));
      }
      return lines.join('\n');
    } catch {
      return '';
    }
  }

  // ai: claude-sonnet-4-6 | 2026-06-27
  async function fetchWellfoundDescription(slug) {
    // Same-origin fetch: content script is on wellfound.com when this runs.
    try {
      const res = await fetch(`https://wellfound.com/jobs/${slug}`, {
        headers: { Accept: 'text/html,application/xhtml+xml' },
      });
      if (!res.ok) return '';
      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      // ai: claude-sonnet-5 | 2026-08-11
      const metadata = extractWellfoundListingMetadata(doc, wellfoundListingIdFromSlug(slug));
      const withMetadata = (text) => (metadata && text ? `${metadata}\n\n${text}` : text || metadata);

      const nextDataEl = doc.getElementById('__NEXT_DATA__');
      if (nextDataEl) {
        try {
          const nd = JSON.parse(nextDataEl.textContent || '{}');
          // Try direct pageProps paths (Wellfound changes these occasionally)
          const jl = nd?.props?.pageProps?.jobListing
                  || nd?.props?.pageProps?.job
                  || nd?.props?.pageProps?.jobListingResult?.jobListing
                  || {};
          const raw = jl.description || jl.descriptionSnippet || jl.jobDescription || '';
          if (raw && raw.length > 100) {
            return withMetadata(raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim());
          }
          // Also try React Query / SWR dehydrated state entries
          for (const q of nd?.props?.pageProps?.dehydratedState?.queries || []) {
            const d = q?.state?.data;
            if (!d || typeof d !== 'object') continue;
            const desc = d.description || d.jobDescription || d.descriptionPlainText
                      || d.jobListing?.description || d.listing?.description || '';
            if (desc && desc.length > 100) return withMetadata(desc.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim());
          }
        } catch { /* fall through to DOM selectors */ }
      }

      const fromDom = longestTextFromDoc(doc, [
        '[class*="job-description"]',
        '[class*="description"] .prose',
        '[class*="jobDescription"]',
        '[data-test="JobDescription"]',
        'main article',
        'main',
      ]);
      if (fromDom.length > 100) return withMetadata(fromDom);

      // Last resort: the fetched page may render client-side, so DOM selectors find nothing.
      // Take the full body text — noisy but gives the importer enough to work with.
      const bodyText = (doc.body?.textContent || '').replace(/\s+/g, ' ').trim();
      if (bodyText.length > 200) return withMetadata(bodyText.slice(0, 8000));

      return metadata;
    } catch {
      return '';
    }
  }

  function buildWellfoundJobPayload() {
    const title = pick(['h1[class*="title"]', 'h1[class*="role"]', '[data-test="JobTitle"]', 'h1']);
    const companyEl = document.querySelector(
      'a[class*="startup-link"], a[class*="company"], [data-test="StartupLink"], ' +
      '[class*="startup"] h2 a, header a[href*="/company/"], a[href*="/company/"]'
    );
    const company = companyEl ? companyEl.innerText.split('\n')[0].trim() : '';
    const description = longestText([
      '[class*="job-description"]', '[class*="description"] .prose',
      '[class*="jobDescription"]', '[data-test="JobDescription"]',
      'section article', 'main article',
    ]);
    // ai: claude-sonnet-5 | 2026-08-11
    const slug = location.pathname.match(/\/jobs\/([^/?#]+)/)?.[1] || '';
    const metadata = extractWellfoundListingMetadata(document, wellfoundListingIdFromSlug(slug));

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + location.href);
    if (metadata) lines.push('', metadata);
    // ai: claude-sonnet-4-6 | 2026-06-26
    if (description) {
      lines.push('', description);
    } else {
      lines.push('', document.body.innerText.trim().slice(0, 12000));
    }

    return JSON.stringify({ jobs: [{ url: location.href, job_title: title, company, listing_text: lines.join('\n') }] });
  }

  async function buildWellfoundSearchPayload() {
    const slugs = collectWellfoundSlugs();
    const total = slugs.length;

    // ai: claude-sonnet-4-6 | 2026-06-27
    // The selected job's full description is already rendered in the right panel of
    // the current DOM — read it directly instead of fetching, since Wellfound's
    // individual job pages render descriptions client-side so a plain HTML fetch
    // returns no usable content.
    const selectedSlug = new URLSearchParams(location.search).get('job_listing_slug') || '';
    let selectedDesc = selectedSlug ? longestText([
      '[class*="job-description"]', '[class*="description"] .prose',
      '[class*="jobDescription"]', '[data-test="JobDescription"]',
      'section article', 'main article',
    ]) : '';
    // ai: claude-sonnet-5 | 2026-08-11
    if (selectedSlug) {
      const selectedMetadata = extractWellfoundListingMetadata(
        document,
        wellfoundListingIdFromSlug(selectedSlug)
      );
      if (selectedMetadata) {
        selectedDesc = selectedDesc ? `${selectedMetadata}\n\n${selectedDesc}` : selectedMetadata;
      }
    }

    const items = slugs.map(slug => {
      const url = 'https://wellfound.com/jobs?job_listing_slug=' + slug;
      let title = '', company = '';
      const link = document.querySelector(`a[href*="/jobs/${slug}"]`);
      if (link) {
        title = link.innerText.split('\n')[0].trim();
        let el = link.parentElement;
        for (let d = 0; d < 6 && el && !company; d++) {
          const cl = el.querySelector('a[href*="/company/"]');
          if (cl && cl !== link) company = cl.innerText.split('\n')[0].trim();
          el = el.parentElement;
        }
        if (!company) {
          const atMatch = title.match(/\bat\s+(.+)$/);
          if (atMatch) company = atMatch[1].trim();
        }
      }
      const description = (slug === selectedSlug && selectedDesc) ? selectedDesc : '';
      return { slug, url, title, company, description };
    });

    for (let i = 0; i < total; i++) {
      if (items[i].description) continue; // already read from current DOM
      sendProgress(`Fetching job details... (${i + 1}/${total})`, (i + 1) / total);
      items[i].description = await fetchWellfoundDescription(items[i].slug);
      if (i < total - 1) await sleep(400);
    }

    const jobs = items.map(({ url, title, company, description }) => {
      const lines = [title];
      if (company) lines.push('Company: ' + company);
      lines.push('URL: ' + url);
      if (description) lines.push('', description);
      return { url, job_title: title, company, listing_text: lines.join('\n') };
    });

    return JSON.stringify({ jobs });
  }

  function buildHnHiringPayload() {
    const seen = new Set();
    const jobs = [];

    document.querySelectorAll('a[href]').forEach(link => {
      const href = link.href || '';
      if (!href.startsWith('http')) return;
      if (href.includes('hnhiring.com') || href.includes('news.ycombinator.com')) return;
      if (seen.has(href)) return;
      try { new URL(href); } catch { return; }
      seen.add(href);

      let container = link.parentElement;
      for (let depth = 0; depth < 12 && container && container !== document.body; depth++) {
        const len = (container.innerText || '').trim().length;
        if (len >= 60 && len <= 6000) break;
        container = container.parentElement;
      }
      if (!container || container === document.body) {
        const text = link.innerText?.trim() || 'Job Posting';
        jobs.push({ url: href, job_title: text, company: '', listing_text: `${text}\nURL: ${href}` });
        return;
      }

      const headings = [...container.querySelectorAll('h1,h2,h3,h4')];
      const company = headings[0]?.innerText?.split('\n')[0]?.trim() || '';
      const title = headings[1]?.innerText?.split('\n')[0]?.trim() || link.innerText?.trim() || '';
      const cardText = (container.innerText || '').trim().slice(0, 2000);

      const lines = [title || company || 'Job Posting'];
      if (company && company !== title) lines.push('Company: ' + company);
      lines.push('URL: ' + href);
      if (cardText) lines.push('', cardText);

      jobs.push({
        url: href,
        job_title: title || company || 'Job Posting',
        company: company !== title ? company : '',
        listing_text: lines.join('\n'),
      });
    });

    return JSON.stringify({ content: JSON.stringify({ jobs }), format: 'json', url: location.href });
  }

  function buildWorkAtAStartupPayload() {
    const seen = new Set();
    const jobs = [];

    document.querySelectorAll('a[href]').forEach(link => {
      const m = link.href.match(/workatastartup\.com\/jobs\/(\d+)/);
      if (!m) return;
      const url = `https://www.workatastartup.com/jobs/${m[1]}`;
      if (seen.has(url)) return;
      seen.add(url);

      let container = link.parentElement;
      for (let depth = 0; depth < 10 && container && container !== document.body; depth++) {
        const len = (container.innerText || '').trim().length;
        if (len >= 80 && len <= 3000) break;
        container = container.parentElement;
      }

      let title = link.innerText?.split('\n')[0]?.trim() || '';
      let company = '';
      if (container) {
        const companyLink = container.querySelector('a[href*="/companies/"]');
        if (companyLink) company = companyLink.innerText?.split('\n')[0]?.trim() || '';
        if (!title) {
          const h = container.querySelector('h1,h2,h3,h4');
          if (h) title = h.innerText?.split('\n')[0]?.trim() || '';
        }
      }

      const cardText = container ? (container.innerText || '').trim().slice(0, 2000) : '';
      const lines = [title || 'Job Posting'];
      if (company) lines.push('Company: ' + company);
      lines.push('URL: ' + url);
      if (cardText) lines.push('', cardText);

      jobs.push({ url, job_title: title || 'Job Posting', company, listing_text: lines.join('\n') });
    });

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  function buildDiscordCareersPayload() {
    const seen = new Set();
    const jobs = [];

    document.querySelectorAll('a[href]').forEach(link => {
      const raw = link.getAttribute('href') || '';
      const m = raw.match(/^\/jobs\/(\d+)/) || link.href.match(/discord\.com\/jobs\/(\d+)/);
      if (!m) return;

      const url = `https://discord.com/jobs/${m[1]}`;
      if (seen.has(url)) return;
      seen.add(url);

      let container = link.parentElement;
      for (let d = 0; d < 10 && container && container !== document.body; d++) {
        const len = (container.innerText || '').trim().length;
        if (len >= 30 && len <= 800) break;
        container = container.parentElement;
      }
      if (!container || container === document.body) container = link.parentElement;

      const headingEl = container?.querySelector('h1,h2,h3,h4,h5');
      const title = headingEl?.innerText?.trim() || link.innerText.trim() || 'Discord Job';
      const cardText = (container?.innerText || '').trim().slice(0, 600);
      const lines = [title, 'Company: Discord', `URL: ${url}`];
      if (cardText && cardText !== title) lines.push('', cardText);

      jobs.push({ url, job_title: title, company: 'Discord', listing_text: lines.join('\n') });
    });

    return JSON.stringify({ jobs, source: { url: 'https://discord.com/careers' } });
  }

  // ai: claude-sonnet-4-6 | 2026-06-28
  // Ashby is a React SPA — window.__appData.posting is populated asynchronously.
  // Poll before reading so we get real job data instead of falling back on raw HTML
  // (which causes the backend to pick up footer/nav links as job postings).
  async function buildAshbyJobPayload() {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (window.__appData?.posting?.title) break;
      await sleep(300);
    }

    const appData = window.__appData;
    const posting = appData?.posting;
    if (!posting?.title) return buildGenericJobPagePayload();

    let company = appData.organization?.name || '';
    if (!company) {
      const slug = window.location.pathname.split('/').filter(Boolean)[0] || '';
      company = slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
    if (!company) {
      company = promptForCompanyName();
    }
    const title = posting.title || '';
    const jobLocation = posting.locationExternalName || posting.locationName || '';
    const department = posting.departmentExternalName || posting.departmentName || '';
    const workplace = posting.workplaceType || '';
    const employment = posting.employmentType || '';
    const desc = posting.descriptionPlainText || posting.description || '';

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + location.href);
    const meta = [department, jobLocation, workplace, employment].filter(Boolean);
    if (meta.length) lines.push(meta.join(' | '));
    if (desc) lines.push('', desc);

    return JSON.stringify({ jobs: [{ url: location.href, job_title: title, company, listing_text: lines.join('\n') }] });
  }

  // ai: claude-sonnet-4-6 | 2026-07-06
  // Company job-board page — turn the filtered jobPostings array (see
  // getAshbyBoardJobs) into one entry per listed posting, linking to that
  // posting's own single-job page via its id.
  function buildAshbySearchPayload() {
    const slug = location.pathname.split('/').filter(Boolean)[0] || '';
    const company = window.__appData?.organization?.name
      || slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    const jobs = getAshbyBoardJobs().map(p => {
      const url = `https://jobs.ashbyhq.com/${slug}/${p.id}`;
      const meta = [p.departmentExternalName || p.departmentName, p.locationExternalName || p.locationName, p.workplaceType, p.employmentType].filter(Boolean);
      const lines = [p.title, 'Company: ' + company, 'URL: ' + url];
      if (meta.length) lines.push(meta.join(' | '));
      return { url, job_title: p.title, company, listing_text: lines.join('\n') };
    });

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  function buildMongoDBCareerPayload() {
    const seen = new Set();
    const jobs = [];

    document.querySelectorAll('a[href*="gh_jid="], a[href*="/careers/job"]').forEach(a => {
      let ghJid = '';
      try { ghJid = new URL(a.href, location.href).searchParams.get('gh_jid') || ''; } catch { return; }
      if (!ghJid || seen.has(ghJid)) return;
      seen.add(ghJid);

      const title = (a.innerText || '').split('\n')[0].trim() || 'Software Engineer';
      const url = `https://job-boards.greenhouse.io/mongodb/jobs/${ghJid}`;
      jobs.push({ url, job_title: title, company: 'MongoDB', listing_text: `${title}\nCompany: MongoDB\nURL: ${url}` });
    });

    return JSON.stringify({ jobs });
  }

  // ai: claude-sonnet-5 | 2026-08-06
  // Handles individual Greenhouse apply pages at my.greenhouse.io, in either observed
  // URL shape: <slug>/jobs/<id> or jobs/<slug>/<id>. Company comes reliably from the
  // URL slug; document.title refines it when the "Job Title at Company Name" pattern
  // is present.
  function buildGreenhouseJobPayload() {
    const pathParts = location.pathname.split('/').filter(Boolean);
    const companySlug = (pathParts[0] === 'jobs' ? pathParts[1] : pathParts[0]) || '';
    let company = companySlug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    const titleAtMatch = document.title.match(/\bat\s+(.+?)(?:\s*[-–|]|$)/i);
    if (titleAtMatch) company = titleAtMatch[1].trim();

    let title = pick(['h1.app-title', 'h1[class*="title"]', 'h1']);
    if (!title) {
      const docTitleMatch = document.title.match(/^(.+?)\s+at\s+/i);
      if (docTitleMatch) title = docTitleMatch[1].trim();
    }
    if (!title) title = document.title.replace(/\s*[-–|].*$/, '').trim();

    const description = longestText([
      '#content', '.content', '#job-details', '.job-description',
      '[class*="job-description"]', '[class*="job-details"]',
      'section', 'main',
    ]);

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + location.href);
    if (description) lines.push('', description);

    return JSON.stringify({
      jobs: [{ url: location.href, job_title: title, company, listing_text: lines.join('\n') }],
    });
  }

  async function buildGreenhouseSearchPayload() {
    const seen = new Set();
    const jobs = [];

    // ai: claude-sonnet-4-6 | 2026-06-25
    // Use a.href (resolved absolute URL) rather than CSS attribute selectors so that
    // relative hrefs like /jobs/12345 on my.greenhouse.io are matched correctly.
    function collectLinks() {
      document.querySelectorAll('a[href]').forEach(a => {
        let u;
        try { u = new URL(a.href); } catch { return; }
        if (!u.hostname.includes('greenhouse.io')) return;
        if (!/\/jobs\/[\w-]{3,}/.test(u.pathname)) return;
        const url = u.origin + u.pathname;
        if (seen.has(url)) return;
        seen.add(url);

        const titleText = (a.innerText || '').split('\n')[0].trim();
        let company = '', el = a.parentElement;
        for (let depth = 0; depth < 6 && el && !company; depth++) {
          const candidate = el.querySelector('[class*="company"], [class*="employer"]');
          if (candidate && candidate !== a) {
            const text = (candidate.innerText || '').split('\n')[0].trim();
            if (text && text !== titleText) company = text;
          }
          el = el.parentElement;
        }

        const lines = [titleText];
        if (company) lines.push(`Company: ${company}`);
        lines.push(`URL: ${url}`);
        jobs.push({ url, job_title: titleText, company, listing_text: lines.join('\n') });
      });
    }

    collectLinks();
    let stallCount = 0;
    while (stallCount < 3) {
      const prevCount = jobs.length;
      sendProgress(`Loading more jobs... (${jobs.length} found)`, undefined);
      window.scrollTo(0, document.body.scrollHeight);
      await sleep(900);
      collectLinks();
      stallCount = jobs.length === prevCount ? stallCount + 1 : 0;
    }

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ─── Phenom People ─────────────────────────────────────────────────────────

  // ai: claude-sonnet-4-6 | 2026-06-17
  // Detects Phenom People search-results pages by the presence of the
  // eagerLoadRefineSearch DDO key that is always SSR'd into an inline script.
  function isPhenomSearchPage() {
    if (!location.pathname.includes('search-results')) return false;
    for (const s of document.querySelectorAll('script:not([src])')) {
      if (s.textContent.includes('eagerLoadRefineSearch')) return true;
    }
    return false;
  }

  // ai: claude-sonnet-4-6 | 2026-06-17
  function phenomTotalHits() {
    for (const s of document.querySelectorAll('script:not([src])')) {
      const m = s.textContent.match(/"totalHits"\s*:\s*(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
    return 0;
  }

  // ai: claude-sonnet-4-6 | 2026-06-17
  // Walk HTML/JS text to extract the JSON object that immediately follows `afterKey`.
  // Properly handles string literals (including escaped characters) so that braces
  // inside string values are not counted toward the depth.
  function extractJsonAfterKey(text, afterKey) {
    const keyIdx = text.indexOf(afterKey);
    if (keyIdx === -1) return null;
    const braceStart = text.indexOf('{', keyIdx + afterKey.length);
    if (braceStart === -1) return null;

    let depth = 0, inStr = false, esc = false;
    for (let i = braceStart; i < text.length; i++) {
      const c = text[i];
      if (esc) { esc = false; continue; }
      if (inStr) { if (c === '\\') esc = true; else if (c === '"') inStr = false; continue; }
      if (c === '"') { inStr = true; continue; }
      if (c === '{') depth++;
      else if (c === '}' && --depth === 0) {
        try { return JSON.parse(text.slice(braceStart, i + 1)); } catch { return null; }
      }
    }
    return null;
  }

  // ai: claude-sonnet-4-6 | 2026-06-17
  // Fetches every page of a Phenom People search-results page and aggregates jobs.
  // Page data is SSR'd into phApp.ddo.eagerLoadRefineSearch inside an inline <script>;
  // we read it from the raw HTML of each fetched page without needing eval or injection.
  async function buildPhenomSearchPayload() {
    const company = (document.title.match(/[-–]\s*(.+?)\s+Careers/i) || [])[1]
      || location.hostname.replace(/^(?:careers|jobs)\./, '').split('.')[0];

    const totalHits = phenomTotalHits();
    if (!totalHits) return buildHtmlPayload();

    // Canonical base URL without pagination params
    const baseUrl = new URL(location.href);
    baseUrl.searchParams.delete('from');
    baseUrl.searchParams.delete('s');

    // Strip "/search-results…" to get the locale prefix for job detail URLs
    const jobBase = location.origin + location.pathname.replace(/\/search-results.*$/, '');

    const PAGE_SIZE = 10; // confirmed from siteConfig "size":"10"
    const totalPages = Math.ceil(totalHits / PAGE_SIZE);
    const seen = new Set();
    const allJobs = [];

    for (let page = 0; page < totalPages; page++) {
      const from = page * PAGE_SIZE;
      sendProgress(
        `Fetching page ${page + 1} of ${totalPages}…`,
        Math.min(from + PAGE_SIZE, totalHits) / totalHits
      );

      const pageUrl = new URL(baseUrl.href);
      if (from > 0) {
        pageUrl.searchParams.set('from', String(from));
        pageUrl.searchParams.set('s', '1');
      }

      let refineSearch = null;
      try {
        const res = await fetch(pageUrl.href, { credentials: 'include' });
        if (!res.ok) break;
        refineSearch = extractJsonAfterKey(await res.text(), '"eagerLoadRefineSearch"');
      } catch { break; }

      if (!refineSearch?.data?.jobs) break;

      for (const job of refineSearch.data.jobs) {
        if (seen.has(job.jobId)) continue;
        seen.add(job.jobId);

        const title = job.title || '';
        const slug = title.toLowerCase()
          .replace(/[–—]/g, '-').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        const url = `${jobBase}/job/${job.jobId}/${slug}`;
        const locs = (job.multi_location?.length ? job.multi_location : [job.location])
          .filter(Boolean).join('; ');

        const lines = [
          title,
          `Company: ${company}`,
          `URL: ${url}`,
          locs && `Location: ${locs}`,
          job.RemoteType && `Work Environment: ${job.RemoteType}`,
          job.descriptionTeaser && `\n${job.descriptionTeaser}`,
        ].filter(Boolean);

        allJobs.push({ url, job_title: title, company, listing_text: lines.join('\n') });
      }

      if (from + PAGE_SIZE < totalHits) await sleep(400);
    }

    return allJobs.length
      ? JSON.stringify({ jobs: allJobs, source: { url: location.href } })
      : buildHtmlPayload();
  }

  // ─── Insight Global (insightglobal.com) ────────────────────────────────────

  // ai: claude-sonnet-5 | 2026-07-28
  // The result count is only rendered as page text ("82 Results for …"), not a
  // data attribute, so it's read off document.body.innerText.
  function insightGlobalTotalHits() {
    const m = document.body.innerText.match(/(\d+)\s+Results?\s+for/i);
    return m ? parseInt(m[1], 10) : 0;
  }

  // ai: claude-sonnet-5 | 2026-07-28
  // Each div.result card embeds a hidden (display:none) <div> holding the full
  // job record as JSON — including the untruncated description, which the
  // visible card only shows a "…More" snippet of. This works identically on
  // pages fetched via fetch()+DOMParser, so no per-job detail request is needed.
  function parseInsightGlobalResult(resultEl) {
    const jobInfo = resultEl.querySelector('div.job-info');
    if (!jobInfo) return null;
    const jsonDiv = [...jobInfo.children].find(
      c => c.tagName === 'DIV' && c.style.display === 'none' && c.textContent.trim().startsWith('{')
    );
    if (!jsonDiv) return null;

    let job;
    try { job = JSON.parse(jsonDiv.textContent.trim()); } catch { return null; }

    const link = resultEl.querySelector('div.job-title a[href]');
    if (!link) return null;
    const url = new URL(link.getAttribute('href'), location.origin).href;

    const isHourly = (job.JobType || []).some(t => /contract/i.test(t));
    const pay = job.SalaryLow && job.SalaryHigh
      ? (isHourly
          ? `$${job.SalaryLow} - $${job.SalaryHigh}/hr`
          : `$${job.SalaryLow.toLocaleString()} - $${job.SalaryHigh.toLocaleString()}/year`)
      : '';
    const postedMatch = /\/Date\((\d+)\)\//.exec(job.PostedDate || '');
    const posted = postedMatch ? new Date(parseInt(postedMatch[1], 10)).toISOString().slice(0, 10) : '';
    const jobLocation = [job.City, job.State].filter(Boolean).join(', ');

    const lines = [
      job.Title || '',
      'Company: Insight Global',
      `URL: ${url}`,
      jobLocation && `Location: ${jobLocation}`,
      job.IsRemote && 'Work Environment: Remote',
      (job.JobType || []).length && `Job Type: ${job.JobType.join(', ')}`,
      pay && `Pay: ${pay}`,
      posted && `Posted: ${posted}`,
      job.Description && `\n${job.Description}`,
    ].filter(Boolean);

    return { url, job_title: job.Title || '', company: 'Insight Global', listing_text: lines.join('\n'), jobId: job.JobID };
  }

  // ai: claude-sonnet-5 | 2026-07-28
  // Pagination is a plain numbered path segment (/jobs/find_a_job/2/, /3/, …)
  // with page 1 having no segment at all — a full page reload per page, not an
  // API or "load more" click. Fetch every page's HTML directly instead.
  async function buildInsightGlobalSearchPayload() {
    const total = insightGlobalTotalHits();
    if (!total) return buildHtmlPayload();

    const pageSize = document.querySelectorAll('div.result').length || 10;
    const totalPages = Math.ceil(total / pageSize);
    const basePath = location.pathname.replace(/\/\d+\/$/, '/');

    const seen = new Set();
    const allJobs = [];

    for (let page = 1; page <= totalPages; page++) {
      sendProgress(`Fetching page ${page} of ${totalPages}…`, page / totalPages);

      const pageUrl = new URL(location.href);
      pageUrl.pathname = page === 1 ? basePath : `${basePath}${page}/`;

      let doc;
      try {
        const res = await fetch(pageUrl.href, { credentials: 'include' });
        if (!res.ok) break;
        doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      } catch { break; }

      const results = doc.querySelectorAll('div.result');
      if (!results.length) break;

      results.forEach(resultEl => {
        const job = parseInsightGlobalResult(resultEl);
        if (!job || seen.has(job.jobId)) return;
        seen.add(job.jobId);
        allJobs.push({ url: job.url, job_title: job.job_title, company: job.company, listing_text: job.listing_text });
      });

      if (page < totalPages) await sleep(400);
    }

    return allJobs.length
      ? JSON.stringify({ jobs: allJobs, source: { url: location.href } })
      : buildHtmlPayload();
  }

  // ─── Insight Global — redesigned split-view search UI ──────────────────────
  // ai: claude-sonnet-5 | 2026-08-24
  // (see the detectPage() comment above for why this can't be told apart by
  // URL). Field labels use Material Symbols ligature icons rendered as plain
  // text nodes ("work Permanent", "payments $1 - $2") rather than separate
  // icon elements, hence stripIgIconLigature below. Neither the list cards
  // nor the detail pane expose the job's requisitionId (GUID) anywhere in the
  // DOM — it only ever surfaces in the URL if the user clicks "Apply", which
  // opens an application dialog and isn't something a capture pass should
  // trigger. So captured jobs use the search page's own URL (fragment-tagged
  // per job title for list mode, to keep multiple captured rows visually
  // distinct) rather than a real per-job deep link.

  function stripIgIconLigature(text) {
    return (text || '').replace(/^\s*(work|payments|favorite|content_copy|location_on)\s+/i, '').trim();
  }

  function extractInsightGlobalSplitDetail() {
    const q = sel => document.querySelector(sel);
    const title = q('[data-testid="job-details-job-title"]')?.textContent.trim() || '';
    const locationEl = q('[data-testid="job-details-worksite-location"]');
    const metaList = locationEl?.closest('ul');
    const jobLocation = metaList
      ? [...metaList.querySelectorAll('li')]
          .filter(li => li.getAttribute('data-testid') !== 'job-details-posted-date')
          .map(li => li.textContent.trim())
          .filter(Boolean)
          .join(' • ')
      : '';
    const posted = (q('[data-testid="job-details-posted-date"]')?.textContent || '')
      .trim().replace(/^Posted\s+/i, '');
    const jobType = stripIgIconLigature(q('[data-testid="job-details-job-type"]')?.textContent);
    const payRate = stripIgIconLigature(q('[data-testid="job-details-pay-rate"]')?.textContent);
    const description = q('[data-testid="job-details-job-description"]')?.innerText.trim() || '';
    return { title, jobLocation, jobType, payRate, posted, description };
  }

  function insightGlobalSplitListingLines({ title, jobLocation, jobType, payRate, posted, description, url }) {
    return [
      title,
      'Company: Insight Global',
      `URL: ${url}`,
      jobLocation && `Location: ${jobLocation}`,
      jobType && `Job Type: ${jobType}`,
      payRate && `Pay: ${payRate}`,
      posted && `Posted: ${posted}`,
      description && `\n${description}`,
    ].filter(Boolean).join('\n');
  }

  // Captures whatever is currently open in the right-hand detail pane — no
  // extra fetch or navigation needed, everything's already rendered.
  function buildInsightGlobalSplitDetailPayload() {
    const detail = extractInsightGlobalSplitDetail();
    const url = location.href;
    const listing_text = insightGlobalSplitListingLines({ ...detail, url });
    return JSON.stringify({
      jobs: [{ url, job_title: detail.title, company: 'Insight Global', listing_text }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-08-24
  // Selecting a card fetches that job's description from the details API and
  // re-renders the pane asynchronously — poll for the title to match rather
  // than assuming the click is synchronous.
  async function waitForInsightGlobalDetailRender(expectedTitle, timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const title = document.querySelector('[data-testid="job-details-job-title"]')?.textContent.trim();
      if (title === expectedTitle) return true;
      await sleep(200);
    }
    return false;
  }

  // Clicking mat-paginator's "next" re-fetches and re-renders the card list
  // client-side; poll the first card's title for a change rather than assume
  // a fixed delay covers it.
  async function waitForInsightGlobalListPageChange(prevFirstTitle, timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const title = document.querySelector('[data-testid="jobs-result-list"] [data-testid="job-card-job-title"]')?.textContent.trim();
      if (title && title !== prevFirstTitle) return true;
      await sleep(200);
    }
    return false;
  }

  // ai: claude-sonnet-5 | 2026-08-24
  // Walks mat-paginator pages rather than replicating IG's filter query
  // language (workTypes=remote, minimumPayRate, distance, … map to a
  // different set of filter.* params server-side, and the mapping isn't
  // fully known) — clicking through reuses whatever filters the page itself
  // already applied. Full descriptions are opt-in (options.fetchFullDescriptions)
  // and capped (options.fetchCap) because each one is a real per-job fetch
  // triggered by selecting that card, same tradeoff as Kforce's search capture.
  async function buildInsightGlobalSplitListPayload(options = {}) {
    const { fetchFullDescriptions = false, fetchCap = 25 } = options;
    const total = insightGlobalTotalHits();
    if (!total) return buildHtmlPayload();

    const allJobs = [];
    let fetched = 0;
    let pageNum = 1;

    while (true) {
      const cards = [...document.querySelectorAll('[data-testid="jobs-result-list"] .job-card')];
      if (!cards.length) break;

      for (const card of cards) {
        const title = card.querySelector('[data-testid="job-card-job-title"]')?.textContent.trim() || '';
        if (!title) continue;
        const jobLocation = (card.querySelector('[data-testid="job-card-worksite-location"]')?.textContent || '')
          .replace(/\s+/g, ' ').trim();
        const jobType = stripIgIconLigature(card.querySelector('[data-testid="job-card-job-type"]')?.textContent);
        const payRate = stripIgIconLigature(card.querySelector('[data-testid="job-card-pay-rate"]')?.textContent);
        const posted = (card.querySelector('[data-testid="job-card-posted-date"]')?.textContent || '')
          .trim().replace(/^Posted\s+/i, '');

        let description = '';
        if (fetchFullDescriptions && fetched < fetchCap) {
          sendProgress(
            `Fetching description ${fetched + 1} of ${Math.min(total, fetchCap)}…`,
            (fetched + 1) / Math.min(total, fetchCap)
          );
          card.click();
          if (await waitForInsightGlobalDetailRender(title)) {
            description = document.querySelector('[data-testid="job-details-job-description"]')?.innerText.trim() || '';
          }
          fetched++;
          await sleep(300);
        }

        const url = `${location.origin}${location.pathname}${location.search}#job=${encodeURIComponent(title)}`;
        allJobs.push({
          url,
          job_title: title,
          company: 'Insight Global',
          listing_text: insightGlobalSplitListingLines({ title, jobLocation, jobType, payRate, posted, description, url }),
        });
      }

      if (allJobs.length >= total) break;
      const nextBtn = document.querySelector('.mat-mdc-paginator-navigation-next');
      if (!nextBtn || nextBtn.disabled) break;

      const prevFirstTitle = cards[0]?.querySelector('[data-testid="job-card-job-title"]')?.textContent.trim();
      pageNum++;
      sendProgress(`Loading page ${pageNum}…`, allJobs.length / total);
      nextBtn.click();
      if (!(await waitForInsightGlobalListPageChange(prevFirstTitle))) break;
    }

    return allJobs.length
      ? JSON.stringify({ jobs: allJobs, source: { url: location.href } })
      : buildHtmlPayload();
  }

  // ─── Kforce (kforce.com) ────────────────────────────────────────────────────

  // ai: claude-sonnet-5 | 2026-07-28
  // Result count only ever shows up as page text ("Displaying 1 - 25 of 471");
  // a search with no hits renders no .data-job-pagination element at all.
  function kforceTotalHits() {
    if (/No search results found/i.test(document.body.innerText)) return 0;
    const countEl = document.querySelector('.data-job-pagination .count');
    const m = countEl && countEl.textContent.match(/of\s+([\d,]+)/i);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : 0;
  }

  // ai: claude-sonnet-5 | 2026-08-23
  // Shared by parseKforceRow's card-snippet capture and buildKforceJobPayload's/
  // the bulk-enrichment loop's full-detail capture, so there's exactly one place
  // that knows the field order of a Kforce listing_text.
  function kforceListingLines({ title, url, jobLocation, jobType, posted, compensation, jobId, description }) {
    return [
      title,
      'Company: Kforce',
      `URL: ${url}`,
      jobLocation && `Location: ${jobLocation}`,
      jobType && `Job Type: ${jobType}`,
      posted && `Posted: ${posted}`,
      compensation && `Compensation: ${compensation}`,
      jobId && `JobID: ${jobId}`,
      description && `\n${description}`,
    ].filter(Boolean).join('\n');
  }

  // ai: claude-sonnet-5 | 2026-08-23
  // Extracts title/meta/description from whatever job detail is currently
  // rendered at `article` — shared by the single-job capture path
  // (buildKforceJobPayload) and the bulk per-row enrichment path below, so
  // there's one place that knows the .data-job-desc/.page-meta selectors.
  function extractKforceJobDetail() {
    const title = document.querySelector('article h1')?.textContent.trim() || '';
    const meta = {};
    document.querySelectorAll('article .page-meta .col-xs-12').forEach(el => {
      const text = el.textContent.replace(/\s+/g, ' ').trim();
      const idx = text.indexOf(':');
      if (idx !== -1) meta[text.slice(0, idx).trim().toLowerCase()] = text.slice(idx + 1).trim();
    });
    const description = document.querySelector('article .data-job-desc')?.innerText.trim() || '';
    return { title, meta, description };
  }

  // ai: claude-sonnet-5 | 2026-08-23
  // Kforce is an Angular SPA routed entirely off location.hash — navigating to
  // a job's #/detail/<id>/ hash re-renders `article .data-job-desc` in place
  // (no page reload), but the render is async, so this polls until the
  // description shows up or timeoutMs elapses. Returns false on timeout
  // instead of throwing, so one slow/broken job doesn't abort the whole batch.
  async function waitForKforceJobRender(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (document.querySelector('article .data-job-desc')?.innerText.trim()) return true;
      await sleep(250);
    }
    return false;
  }

  // ai: claude-sonnet-5 | 2026-08-23
  // Uses replaceState + a synthetic hashchange event, rather than assigning
  // location.hash directly, so walking through dozens of search results for
  // full-description capture doesn't push one browser-history entry per job.
  function navigateKforceHash(hash) {
    const oldURL = location.href;
    const newURL = `${location.pathname}${location.search}${hash}`;
    history.replaceState(null, '', newURL);
    window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL, newURL: location.href }));
  }

  // ai: claude-sonnet-5 | 2026-08-23
  // Confirmed live against kforce.com: Angular's router only re-resolves
  // `article .data-job-desc` on a list→detail transition. Navigating straight
  // from one #/detail/<id>/ hash to another #/detail/<id>/ hash (the obvious
  // approach) leaves the DOM showing the *previous* job — h1/description
  // don't update, and waitForKforceJobRender still reports "rendered" because
  // stale content is already non-empty. Bouncing back through the search hash
  // before each detail hash reliably forces a fresh render every time.
  async function navigateToKforceDetail(searchHash, detailHash) {
    navigateKforceHash(searchHash);
    await sleep(500);
    navigateKforceHash(detailHash);
    return waitForKforceJobRender();
  }

  // ai: claude-sonnet-5 | 2026-07-28
  function parseKforceRow(li) {
    const link = li.querySelector('h2 a.linkForJob');
    if (!link) return null;
    const title = link.textContent.trim();
    const url = new URL(link.getAttribute('href'), location.href).href;
    const posted = li.querySelector('h2 span')?.textContent.trim() || '';
    const snippet = li.querySelector('.col-md-10 p')?.textContent.replace(/\s+/g, ' ').trim() || '';
    const jobType = li.querySelector('.job-list-detail .type')?.textContent.trim() || '';
    const jobLocation = li.querySelector('.job-list-detail .locations')?.textContent.trim() || '';

    return {
      url, job_title: title, company: 'Kforce',
      // Kept off the outgoing wire payload (see buildKforceSearchPayload's
      // final .map) — only needed internally to rebuild listing_text if this
      // row gets a full-description enrichment pass.
      _title: title, _jobLocation: jobLocation, _jobType: jobType, _posted: posted,
      listing_text: kforceListingLines({ title, url, jobLocation, jobType, posted, description: snippet }),
    };
  }

  // ai: claude-sonnet-5 | 2026-07-28
  // Single Kforce job page. There's no JSON-LD block, so title/location/type/
  // compensation/JobID are read off the rendered .page-meta columns. The URL
  // must keep location.hash — every job on this Angular SPA shares the same
  // pathname (/find-work/search-jobs/), so the hash (#/detail/<id>/) is the
  // only part of the URL that actually identifies the posting.
  function buildKforceJobPayload() {
    const { title, meta, description } = extractKforceJobDetail();
    const url = location.origin + location.pathname + location.hash;
    const listing_text = kforceListingLines({
      title,
      url,
      jobLocation: meta.location || '',
      jobType: meta['job type'] || '',
      posted: '',
      compensation: meta.compensation || '',
      jobId: meta.jobid || '',
      description,
    });

    return JSON.stringify({
      jobs: [{ url, job_title: title, company: 'Kforce', listing_text }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-07-28
  // "Load more" appends the next 25 results client-side rather than navigating
  // to a numbered page or a fetchable API endpoint (confirmed: the site's own
  // search calls an Azure Search index directly with an api-key header, which
  // this isolated-world content script can't reuse — Chrome only exposes the
  // page's own fetch/XHR calls to a script injected in the MAIN world, and
  // this one runs isolated per manifest defaults). Pagination is therefore
  // driven by repeatedly clicking the button and waiting for new rows.
  //
  // Only the truncated card snippet is available per job here by default, not
  // the full description: unlike Jibe/Insight Global, a per-job fetch() can't
  // get the full text either, because every job's content is rendered
  // client-side by Angular reading location.hash — the fetched HTML would
  // just be the same empty SPA shell. Opening a job (which flips detectPage
  // to kforceJob) is the only reliable way to get its full description — so
  // when options.fetchFullDescriptions is set, this walks the tab through
  // each row's #/detail/<id>/ hash in place (see navigateKforceHash) to
  // collect it, capped at options.fetchCap since that's a real per-job page
  // load against Kforce's site and can't be parallelized or done in the
  // background.
  async function buildKforceSearchPayload(options = {}) {
    const { fetchFullDescriptions = false, fetchCap = 25 } = options;
    const total = kforceTotalHits();
    if (!total) return buildHtmlPayload();

    const originalHash = location.hash;

    const maxClicks = Math.ceil(total / 25) + 2;
    for (let i = 0; i < maxClicks; i++) {
      const rowsBefore = document.querySelectorAll('ul.data-jobs > li.row').length;
      if (rowsBefore >= total) break;

      const loadMore = [...document.querySelectorAll('.data-job-pagination span')]
        .find(s => s.textContent.trim() === 'Load more');
      if (!loadMore) break;

      sendProgress(`Loading jobs… (${rowsBefore}/${total})`, rowsBefore / total);
      loadMore.click();

      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        await sleep(400);
        if (document.querySelectorAll('ul.data-jobs > li.row').length > rowsBefore) break;
      }
    }

    const jobs = [...document.querySelectorAll('ul.data-jobs > li.row')]
      .map(parseKforceRow)
      .filter(Boolean);

    if (fetchFullDescriptions && jobs.length) {
      const limit = Math.min(fetchCap, jobs.length);
      for (let i = 0; i < limit; i++) {
        const job = jobs[i];
        sendProgress(`Fetching full description ${i + 1} of ${limit}…`, (i + 1) / limit);
        try {
          const rendered = await navigateToKforceDetail(originalHash, new URL(job.url).hash);
          if (rendered) {
            const { meta, description } = extractKforceJobDetail();
            if (description) {
              job.listing_text = kforceListingLines({
                title: job._title,
                url: job.url,
                jobLocation: job._jobLocation,
                jobType: job._jobType,
                posted: job._posted,
                compensation: meta.compensation || '',
                jobId: meta.jobid || '',
                description,
              });
            }
          }
        } catch {
          // Keep this row's short card snippet — one bad job shouldn't sink the batch.
        }
        await sleep(700); // politeness delay between per-job SPA navigations
      }
      // Leave the tab back on the search list, not sitting on the last job
      // viewed. .data-job-desc doesn't exist on the list route (confirmed:
      // Angular unmounts it entirely), so there's nothing to poll for here —
      // just give the list a moment to re-render before returning.
      navigateKforceHash(originalHash);
      await sleep(800);
    }

    const wireJobs = jobs.map(({ url, job_title, company, listing_text }) => (
      { url, job_title, company, listing_text }
    ));

    return wireJobs.length
      ? JSON.stringify({ jobs: wireJobs, source: { url: location.href } })
      : buildHtmlPayload();
  }

  // ─── Jibe / iCIMS (careers.marriott.com, …) ────────────────────────────────

  // ai: claude-fable-5 | 2026-07-13
  const _JIBE_JOB_PATH_RE = /\/job\/([0-9A-Fa-f]{32})(?:\/|$)/;

  // ai: claude-fable-5 | 2026-07-13
  // Returns the schema.org JobPosting object from a document's JSON-LD blocks,
  // or null. Jibe job pages (and many other ATSes) embed one with the full
  // description, hiring organization, and req ID.
  function readJobPostingLd(doc) {
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(s.textContent || '');
        const items = Array.isArray(data) ? data : (data['@graph'] || [data]);
        for (const item of items) {
          if (item && item['@type'] === 'JobPosting') return item;
        }
      } catch { /* skip malformed block */ }
    }
    return null;
  }

  // ai: claude-fable-5 | 2026-07-13
  // DOMParser does not execute scripts, so this is safe on fetched HTML strings.
  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return (doc.body?.textContent || '').replace(/\s+/g, ' ').trim();
  }

  // ai: claude-fable-5 | 2026-07-13
  function companyFromHostname() {
    const base = location.hostname.replace(/^(?:careers|jobs|www)\./, '').split('.')[0];
    return base.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  // ai: claude-fable-5 | 2026-07-13
  // Harvests job cards from the live DOM of a Jibe listing page. The filtered
  // search results render client-side, so this must run against the rendered
  // page, not fetched HTML. Card text carries "Req ID: NNN".
  function collectJibeJobLinks() {
    const seen = new Map(); // hex id → card info
    document.querySelectorAll('a[href]').forEach(a => {
      let u;
      try { u = new URL(a.href); } catch { return; }
      if (u.hostname !== location.hostname) return;
      const m = u.pathname.match(_JIBE_JOB_PATH_RE);
      if (!m) return;
      const hexId = m[1].toUpperCase();
      if (seen.has(hexId)) return;

      let container = a.parentElement;
      for (let d = 0; d < 8 && container && container !== document.body; d++) {
        const len = (container.innerText || '').trim().length;
        if (len >= 40 && len <= 1500) break;
        container = container.parentElement;
      }
      const cardText = (container?.innerText || '').trim();
      const reqId = (cardText.match(/\bReq\s*ID:?\s*(\d+)/i) || [])[1] || '';
      const title = (a.innerText || '').split('\n')[0].trim()
        || container?.querySelector('h1,h2,h3,h4')?.innerText?.split('\n')[0]?.trim()
        || '';
      // Canonical URL without query — strips utm_* tracking params so the same
      // job captured twice dedupes by URL.
      seen.set(hexId, { url: u.origin + u.pathname, title, reqId, cardText: cardText.slice(0, 500) });
    });
    return [...seen.values()];
  }

  // ai: claude-fable-5 | 2026-07-13
  function jibeJobLines({ title, company, reqId, url, description, fallbackText }) {
    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    if (reqId) lines.push('Req ID: ' + reqId);
    lines.push('URL: ' + url);
    if (description) lines.push('', description);
    else if (fallbackText) lines.push('', fallbackText);
    return lines.join('\n');
  }

  // ai: claude-fable-5 | 2026-07-13
  // Single Jibe job page — prefer the JSON-LD JobPosting block; fall back to DOM.
  function buildJibeJobPayload() {
    const posting = readJobPostingLd(document);
    const title = posting?.title || pick(['h1[class*="job"]', 'h1[class*="title"]', 'h1']);
    const company = posting?.hiringOrganization?.name || companyFromHostname();
    const reqId = posting?.identifier?.value
      || (document.body.innerText.match(/\b(?:Job|Req)\s*ID:?\s*(\d+)/i) || [])[1]
      || '';
    const description = posting?.description
      ? htmlToText(posting.description)
      : document.body.innerText.trim().slice(0, 12000);
    const url = location.origin + location.pathname;

    return JSON.stringify({
      jobs: [{
        url,
        job_title: title,
        company,
        listing_text: jibeJobLines({ title, company, reqId, url, description }),
      }],
      source: { url: location.href },
    });
  }

  // ai: claude-fable-5 | 2026-07-13
  // Jibe listing page — harvest cards from the live DOM, then fetch each job's
  // page (same-origin) and pull the full description from its JSON-LD block.
  async function buildJibeSearchPayload() {
    const items = collectJibeJobLinks();
    const jobs = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      sendProgress(`Fetching job details... (${i + 1}/${items.length})`, (i + 1) / items.length);

      let posting = null;
      try {
        const res = await fetch(item.url, { headers: { Accept: 'text/html,application/xhtml+xml' } });
        if (res.ok) posting = readJobPostingLd(new DOMParser().parseFromString(await res.text(), 'text/html'));
      } catch { /* keep card-level data */ }

      const title = posting?.title || item.title;
      const company = posting?.hiringOrganization?.name || companyFromHostname();
      const reqId = posting?.identifier?.value || item.reqId;
      const description = posting?.description ? htmlToText(posting.description) : '';

      jobs.push({
        url: item.url,
        job_title: title,
        company,
        listing_text: jibeJobLines({ title, company, reqId, url: item.url, description, fallbackText: item.cardText }),
      });
      if (i < items.length - 1) await sleep(400);
    }

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ai: claude-sonnet-4-6 | 2026-06-20
  // Jobanni uses Next.js App Router with RSC.  The search results page streams
  // job data (including companyName) via the RSC navigation endpoint, exactly
  // as the server-side scanner does.  Fetching that endpoint from the content
  // script (same origin → no CORS) gives us accurate company names that the
  // generic DOM-walk cannot reliably extract.
  async function buildJobanniPayload() {
    sendProgress('Connecting to Jobanni RSC API…', 5);

    // Extract the Next.js build hash — it appears in _buildManifest.js script URLs.
    let buildHash = null;
    document.querySelectorAll('script[src*="/_next/static/"]').forEach(s => {
      const m = (s.src || '').match(/\/static\/([^/]+)\/_buildManifest/);
      if (m) buildHash = m[1];
    });
    if (!buildHash && window.__NEXT_DATA__?.buildId) {
      buildHash = window.__NEXT_DATA__.buildId;
    }
    if (!buildHash) {
      return buildSmartGenericPayload();  // fallback
    }

    // Build the RSC endpoint URL (same path+query, plus _rsc=<buildHash>).
    const parsed = new URL(location.href);
    const navSearch = parsed.search;         // keep original query params
    const rscParams = new URLSearchParams(navSearch);
    rscParams.set('_rsc', buildHash);
    const rscUrl = `${parsed.origin}${parsed.pathname}?${rscParams}`;
    const nextUrl = parsed.pathname + navSearch;

    let rscText;
    try {
      const res = await fetch(rscUrl, {
        headers: { 'RSC': '1', 'Next-Url': nextUrl },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      rscText = await res.text();
    } catch {
      return buildSmartGenericPayload();  // fallback
    }

    sendProgress('Parsing Jobanni job data…', 40);

    // Locate the {"total":N,"jobs":[...]} object in the RSC stream.
    const jobsStart = rscText.indexOf('{"total":');
    if (jobsStart === -1) return buildSmartGenericPayload();

    // Walk braces to find the matching closing brace (handles nested JSON cleanly).
    let depth = 0, inStr = false, esc = false, jobsEnd = -1;
    for (let i = jobsStart; i < rscText.length; i++) {
      const ch = rscText[i];
      if (esc) { esc = false; continue; }
      if (ch === '\\' && inStr) { esc = true; continue; }
      if (ch === '"') { inStr = !inStr; continue; }
      if (inStr) continue;
      if (ch === '{') depth++;
      else if (ch === '}' && --depth === 0) { jobsEnd = i + 1; break; }
    }
    if (jobsEnd === -1) return buildSmartGenericPayload();

    let jobsData;
    try {
      jobsData = JSON.parse(rscText.slice(jobsStart, jobsEnd));
    } catch {
      return buildSmartGenericPayload();
    }

    const rawJobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];
    if (!rawJobs.length) return buildSmartGenericPayload();

    sendProgress(`Found ${rawJobs.length} jobs, extracting descriptions…`, 60);

    // Resolve "$ref" description pointers to their T-format text blocks.
    // The blocks appear in the RSC stream after the jobs object: {ref}:T{hex_len},{text}
    const afterJobs = rscText.slice(jobsEnd);
    const descMap = {};
    rawJobs.forEach(job => {
      if (typeof job.description !== 'string' || !job.description.startsWith('$')) return;
      const ref = job.description.slice(1);
      const escaped = ref.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const m = afterJobs.match(new RegExp(escaped + ':T([0-9a-f]+),'));
      if (!m) return;
      const textLen = parseInt(m[1], 16);
      const blockStart = afterJobs.indexOf(m[0]) + m[0].length;
      const rawHtml = afterJobs.slice(blockStart, blockStart + textLen);
      descMap[ref] = rawHtml
        .replace(/<[^>]+>/g, ' ')
        .replace(/&(?:[a-z]+|#\d+);/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    });

    const jobs = rawJobs
      .map(job => {
        const url = (job.url || '').trim();
        if (!url) return null;
        const descRef = typeof job.description === 'string' && job.description.startsWith('$')
          ? job.description.slice(1) : null;
        const listingText = descRef ? (descMap[descRef] || '') : '';
        return {
          url,
          job_title: (job.title || '').trim(),
          company:   (job.companyName || '').trim(),
          listing_text: listingText,
          platform: 'Jobanni',
        };
      })
      .filter(Boolean);

    if (!jobs.length) return buildSmartGenericPayload();

    sendProgress(`Sending ${jobs.length} jobs…`, 80);
    return JSON.stringify({
      jobs,
      source: { url: location.href, name: 'Jobanni', platform: 'jobanni' },
    });
  }

  // Generic job-link harvester — works on Phenom People (Cisco), Workday, Lever,
  // and any other career-site structure the specialised builders don't cover.
  function buildSmartGenericPayload() {
    const seen = new Set();
    const jobs = [];

    document.querySelectorAll('a[href]').forEach(link => {
      let u;
      try { u = new URL(link.href); } catch { return; }
      if (!isJobLink(u)) return;
      const url = u.origin + u.pathname; // strip query/hash to deduplicate
      if (seen.has(url)) return;
      seen.add(url);

      // Walk up to find a card-sized container (20–1500 chars of visible text).
      let container = link.parentElement;
      for (let d = 0; d < 8 && container && container !== document.body; d++) {
        const len = (container.innerText || '').trim().length;
        if (len >= 20 && len <= 1500) break;
        container = container.parentElement;
      }

      // Prefer Phenom People data attributes, then headings, then link text.
      const titleEl =
        container?.querySelector('[data-ph-at-id*="title"]') ||
        container?.querySelector('h1,h2,h3,h4') ||
        link;
      const title = titleEl?.innerText?.split('\n')[0]?.trim() || '';

      const locationEl = container?.querySelector(
        '[data-ph-at-id*="location"], [class*="location"], [data-automation-id*="location"]'
      );
      const jobLocation = locationEl?.innerText?.split('\n')[0]?.trim() || '';

      const cardText = container ? (container.innerText || '').trim().slice(0, 500) : '';
      const lines = [title || url.split('/').filter(Boolean).pop()];
      if (jobLocation) lines.push('Location: ' + jobLocation);
      lines.push('URL: ' + link.href); // use original href so redirect params are preserved
      if (cardText && cardText !== title) lines.push('', cardText);

      jobs.push({ url: link.href, job_title: title, company: '', listing_text: lines.join('\n') });
    });

    // ai: claude-sonnet-4-6 | 2026-07-09
    // The currently-open page can itself be a job posting (e.g. a job detail
    // page that also lists "related jobs" elsewhere on the page). Job detail
    // pages rarely link to themselves, so anchor harvesting above won't find
    // it — add it explicitly using the page's own title, not anchor text.
    let selfUrl;
    try { selfUrl = new URL(location.href); } catch { selfUrl = null; }
    if (selfUrl && isJobLink(selfUrl)) {
      const selfKey = selfUrl.origin + selfUrl.pathname;
      if (!seen.has(selfKey)) {
        seen.add(selfKey);
        const title = pick(['[data-ph-at-id*="title"]', 'h1[class*="job"]', 'h1[class*="title"]', 'h1']) || '';
        const bodyText = document.body.innerText.trim().slice(0, 12000);
        const lines = [title || selfKey.split('/').filter(Boolean).pop()];
        lines.push('URL: ' + location.href);
        if (bodyText) lines.push('', bodyText);
        jobs.push({ url: location.href, job_title: title, company: '', listing_text: lines.join('\n') });
      }
    }

    if (jobs.length === 0) return buildHtmlPayload();

    // ai: claude-sonnet-4-6 | 2026-06-08
    if (jobs.length === 1 && !jobs[0].company) {
      const name = promptForCompanyName();
      if (name) jobs[0].company = name;
    }
    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ai: claude-sonnet-5 | 2026-08-02
  // WTTJ periodically shows a "Did you apply?" modal (data-testid="modal",
  // role="dialog") nagging about a completely unrelated previous job the user
  // viewed — it renders as an overlay outside <main>/<article>, so selector-based
  // extraction misses it, but the document.body.innerText fallback (used when no
  // selector matches) picks up its text and prepends irrelevant company/title
  // text ahead of the real JD, which can confuse fit scoring. Hide it for the
  // duration of the text read (not removed — WTTJ's own JS may still reference
  // it) rather than clicking "Yes"/"No", which would record an unintended answer.
  function bodyTextExcludingWttjModal() {
    const modal = document.querySelector('[data-testid="modal"], [role="dialog"]');
    if (!modal) return document.body.innerText.trim();
    const prevDisplay = modal.style.display;
    modal.style.display = 'none';
    const text = document.body.innerText.trim();
    modal.style.display = prevDisplay;
    return text;
  }

  // ai: claude-sonnet-4-6 | 2026-06-27
  // WTTJ is a Next.js SPA. Email-link URLs (/jobs/<id>) trigger a client-side
  // redirect and async API fetch; the DOM is often not ready when content.js first
  // runs. Poll until an h1 appears (up to 10s), then extract.
  // descContent is kept separate from metadata so the body-text fallback can fire
  // when the DOM is empty — unlike the old approach where description was always
  // set to at least the URL, making the fallback unreachable.
  async function buildWttjJobPayload() {
    const pollDeadline = Date.now() + 10000;
    while (Date.now() < pollDeadline) {
      const h1 = document.querySelector('h1');
      if (h1 && h1.innerText.trim().length > 3) break;
      await sleep(500);
    }

    let title = '', company = '', contractType = '', jobLocation = '', descContent = '';

    // __NEXT_DATA__ is the most reliable source when the page is server-side rendered.
    const nextData = document.getElementById('__NEXT_DATA__');
    if (nextData) {
      try {
        const parsed = JSON.parse(nextData.textContent || '');
        const props = parsed?.props?.pageProps || {};
        // Field names vary by locale/version; try common ones.
        const job = props.job || props.jobOffer || props.jobListing || props.data?.job || {};
        const org = props.organization || props.company || props.employer || {};

        title = job.name || job.title || '';
        company = org.name || org.title || job.organization?.name || '';
        contractType = job.contract_type?.label || job.contractType?.label || '';
        jobLocation = (job.offices || []).map(o => o.city || o.name).filter(Boolean).join(', ')
          || (job.remote ? 'Remote' : '');
        const tasks = (job.tasks_attributes || []).map(t => t.description).join('\n');
        const profile = (job.profile_attributes || []).map(t => t.description).join('\n');
        descContent = [tasks, profile].filter(Boolean).join('\n\n');
      } catch { /* fall through to DOM extraction */ }
    }

    if (!title) {
      title = pick(['h1[class*="job"]', 'h1[class*="title"]', '[data-testid="job-title"]', 'h1']);
    }
    if (!company) {
      company = pick([
        '[data-testid="company-name"]',
        '[class*="organization-name"]',
        'a[href*="/companies/"] h2',
        'a[href*="/companies/"]',
      ]);
    }
    if (!descContent) {
      descContent = longestText([
        '[data-testid="job-description"]',
        '[class*="job-description"]',
        'section[class*="description"]',
        'article',
        'main',
      ]);
    }
    // Fall back to full page text so the importer always gets enough content,
    // even when DOM selectors find nothing after the poll timeout.
    if (!descContent) {
      descContent = bodyTextExcludingWttjModal().slice(0, 12000);
    }

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push(`Company: ${company}`);
    const meta = [contractType, jobLocation].filter(Boolean);
    if (meta.length) lines.push(meta.join(' | '));
    lines.push(`URL: ${location.href}`);
    if (descContent) lines.push('', descContent);

    return JSON.stringify({
      jobs: [{
        url: location.href,
        job_title: title,
        company,
        listing_text: lines.join('\n'),
      }],
      source: { url: location.href },
    });
  }

  // ─── Jobgether ─────────────────────────────────────────────────────────────

  // ai: claude-sonnet-5 | 2026-07-23
  // The match-score widget (button#mobile-match-feedback, data-match-score="N")
  // renders client-side after initial paint, so the button may not exist yet
  // when this runs — poll briefly, same pattern as the Ashby/Wellfound waits.
  // Jobgether only reveals the numeric score for roles scoring >=50%; below
  // that it shows a fixed "Not a fit" badge with data-match-score forced to 0
  // rather than the real value, so that case is reported as text, not a number.
  function extractJobgetherMatchScore() {
    const btn = document.querySelector('[data-match-score]');
    if (!btn) return '';

    const notFitEl = btn.querySelector('[id*="badge-not-fit"]');
    if (notFitEl && !notFitEl.classList.contains('hidden')) {
      return 'Your match score: Not a fit for this role (below 50%)';
    }

    const scoreTextEl = btn.querySelector('[id*="score-text"]');
    const scoreAttr = btn.getAttribute('data-match-score');
    const scoreText = (scoreTextEl?.innerText || '').trim() || (scoreAttr ? `${scoreAttr}%` : '');
    return scoreText && scoreText !== '0%' ? `Your match score: ${scoreText}` : '';
  }

  // ai: claude-sonnet-5 | 2026-08-02
  // The <title> "Job Title at Company Name" pattern (see below) is populated
  // client-side and isn't guaranteed to have updated by the time we read it —
  // job 3697 (Obsidian Security) shipped with document.title still missing
  // the "at Company" suffix, leaving company ''. The header also renders a
  // dedicated company link (<a href="/remote-jobs/company-<slug>" class="underline">
  // Company Name</a>) immediately after the h1, which is present as soon as the
  // job data itself has loaded — use it as the primary source. The same href
  // pattern also appears on unrelated "See all jobs" links further down the
  // page, so skip those and take the first link with non-"see all jobs" text.
  function extractJobgetherCompanyFromLink() {
    const links = document.querySelectorAll('a[href*="/remote-jobs/company-"]');
    for (const link of links) {
      const text = link.innerText.trim();
      if (text && !/^see all jobs/i.test(text)) return text;
    }
    return '';
  }

  // ai: claude-sonnet-5 | 2026-07-27
  // Company was previously always sent as '', leaving it entirely to the
  // server-side LLM to guess from the page body — which repeats the title and
  // company as bare adjacent lines with no label ("Staff Software Engineer \n
  // Rocket Companies"), and occasionally swapped which line was which. Confirmed
  // live against multiple jobgether.com/offer/ postings that document.title
  // reliably follows "Job Title at Company Name" (same pattern already used for
  // Greenhouse in buildGreenhouseJobPayload above), so use it as a fallback hint
  // when the company link above isn't found.
  async function buildJobgetherJobPayload() {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      if (document.querySelector('[data-match-score]')) break;
      await sleep(300);
    }

    let title = pick(['h1[class*="title"]', 'h1', '[data-testid*="title"]']);
    let company = extractJobgetherCompanyFromLink();
    const titleAtMatch = document.title.match(/\bat\s+(.+?)(?:\s*[-–|]|$)/i);
    if (!company && titleAtMatch) company = titleAtMatch[1].trim();
    if (!title) {
      const docTitleMatch = document.title.match(/^(.+?)\s+at\s+/i);
      if (docTitleMatch) title = docTitleMatch[1].trim();
    }

    const matchScoreLine = extractJobgetherMatchScore();
    const bodyText = document.body.innerText.trim().slice(0, 12000);

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + location.href);
    if (matchScoreLine) lines.push(matchScoreLine);
    if (bodyText) lines.push('', bodyText);

    return JSON.stringify({
      jobs: [{ url: location.href, job_title: title, company, listing_text: lines.join('\n') }],
      source: { url: location.href },
    });
  }

  // ─── Build Dispatcher ──────────────────────────────────────────────────────

  // ai: claude-sonnet-4-6 | 2026-07-09
  // Stamp every outgoing payload with the extension's own version so captures
  // can be traced back to the content-script build that produced them.
  function withExtensionVersion(payloadJson) {
    try {
      const parsed = JSON.parse(payloadJson);
      parsed.extension_version = chrome.runtime.getManifest().version;
      return JSON.stringify(parsed);
    } catch {
      return payloadJson;
    }
  }

  async function buildPayload(options = {}) {
    const page = detectPage();
    let payload;
    switch (page.type) {
      case 'linkedInJob':      payload = await buildLinkedInJobPayload(); break;
      case 'linkedInSearch':
        payload = options.captureMode === 'detail'
          ? await buildLinkedInSearchDetailPayload()
          : buildLinkedInSearchPayload();
        break;
      case 'wellfoundJob':     payload = buildWellfoundJobPayload(); break;
      case 'wellfoundSearch':  payload = await buildWellfoundSearchPayload(); break;
      case 'greenhouseJob':    payload = buildGreenhouseJobPayload(); break;
      case 'greenhouseSearch': payload = await buildGreenhouseSearchPayload(); break;
      case 'mongoDBCareer':    payload = buildMongoDBCareerPayload(); break;
      case 'hnHiring':         payload = buildHnHiringPayload(); break;
      case 'workAtAStartup':   payload = buildWorkAtAStartupPayload(); break;
      case 'discordCareers':   payload = buildDiscordCareersPayload(); break;
      case 'ashbyJob':         payload = await buildAshbyJobPayload(); break;
      case 'ashbySearch':      payload = buildAshbySearchPayload(); break;
      case 'wttjJob':          payload = buildWttjJobPayload(); break;
      case 'jibeJob':          payload = buildJibeJobPayload(); break;
      case 'jibeSearch':       payload = await buildJibeSearchPayload(); break;
      case 'phenomSearch':     payload = await buildPhenomSearchPayload(); break;
      case 'insightGlobalSearch': payload = await buildInsightGlobalSearchPayload(); break;
      case 'insightGlobalSplit':
        payload = options.captureMode === 'detail'
          ? buildInsightGlobalSplitDetailPayload()
          : await buildInsightGlobalSplitListPayload(options);
        break;
      case 'kforceJob':        payload = buildKforceJobPayload(); break;
      case 'kforceSearch':     payload = await buildKforceSearchPayload(options); break;
      case 'jobanni':          payload = await buildJobanniPayload(); break;
      case 'jobgetherJob':     payload = await buildJobgetherJobPayload(); break;
      case 'smartGeneric':     payload = buildSmartGenericPayload(); break;
      // ai: claude-sonnet-4-6 | 2026-06-26
      // For pages that don't match a known platform, extract body text rather
      // than sending raw HTML. The HTML import path scans for job links, which
      // yields nothing for individual job pages. Body text gives the importer
      // enough content to extract the description.
      default:                 payload = await buildGenericJobPagePayload(); break;
    }
    return withExtensionVersion(payload);
  }

  // ai: claude-sonnet-5 | 2026-08-12
  // Job pages built on MUI (and similar) accordions keep "What is the role",
  // "What you bring", etc. collapsed behind aria-expanded="false" toggles —
  // <details> works the same way natively. innerText only reflects rendered
  // (visible) text, so an unexpanded section is silently absent from the
  // capture even though the DOM node exists. Force everything open first.
  // Skip nav/header toggles so this doesn't fire mobile menus etc.
  async function expandCollapsedSections() {
    document.querySelectorAll('details:not([open])').forEach(el => { el.open = true; });
    const toggles = [...document.querySelectorAll('[aria-expanded="false"]')]
      .filter(el => !el.closest('nav, header, [role="navigation"]'));
    if (toggles.length === 0) return;
    toggles.forEach(el => el.click());
    await new Promise(resolve => setTimeout(resolve, 400));
  }

  async function buildGenericJobPagePayload() {
    await expandCollapsedSections();
    const title = pick([
      'h1[class*="job"]', 'h1[class*="title"]',
      '[data-testid*="title"]', '[data-testid*="job-title"]',
      'h1',
    ]);
    const bodyText = document.body.innerText.trim().slice(0, 12000);
    const lines = [];
    if (title) lines.push(title);
    lines.push('URL: ' + location.href);
    if (bodyText) lines.push('', bodyText);
    return JSON.stringify({
      jobs: [{ url: location.href, job_title: title, company: '', listing_text: lines.join('\n') }],
      source: { url: location.href },
    });
  }

  // ─── Message Listener ──────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.action === 'scan') {
      sendResponse(detectPage());
      return false;
    }

    if (message.action === 'build') {
      buildPayload(message.options || {})
        .then(payload => sendResponse({ payload }))
        .catch(err => sendResponse({ error: String(err) }));
      return true; // keep channel open for async response
    }

    // Context menu flow: background can't wait for a long async response,
    // so content script sends the result back proactively via runtime message.
    if (message.action === 'buildForBackground') {
      buildPayload()
        .then(payload => chrome.runtime.sendMessage({ action: 'payloadReady', payload }))
        .catch(() => chrome.runtime.sendMessage({ action: 'payloadReady', payload: null }));
      return false;
    }

    // ai: claude-sonnet-4-6 | 2026-07-02
    // Closed-req check flow: capture the raw authenticated page instead of running
    // the site-specific job-listing extraction used by buildForBackground.
    if (message.action === 'buildClosedReqCheck') {
      buildClosedReqCheckPayload()
        .then(payload => chrome.runtime.sendMessage({ action: 'payloadReady', payload }))
        .catch(() => chrome.runtime.sendMessage({ action: 'payloadReady', payload: null }));
      return false;
    }
  });
})();
