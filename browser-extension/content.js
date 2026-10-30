/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// Guard: only register listeners once per tab, even if executeScript is called again.
(function () {
  'use strict';

  if (window.__jhaContentLoaded) return;
  window.__jhaContentLoaded = true;

  // ai: claude-sonnet-5 | 2026-08-25
  // Shared by detectPage's job-count and buildGreenhouseSearchPayload's link
  // collector. A loose "/jobs/<3+ word chars>" test also matches the search
  // page's own nav links (e.g. /jobs/search), which aren't real job postings
  // and only yield the SPA shell when fetched — this requires the trailing
  // numeric job id that both real Greenhouse single-job path shapes share.
  const _GH_JOB_PATH_RE = /^\/[^/]+\/jobs\/\d+(?:[/?#]|$)|^\/jobs\/[^/]+\/\d+(?:[/?#]|$)/;

  const _GH_PUBLIC_BOARD_HOSTS = new Set(['job-boards.greenhouse.io', 'boards.greenhouse.io']);

  // ai: claude-opus-5-5 | 2026-10-27
  // A company's own careers page can show one Greenhouse posting through
  // Greenhouse's JS embed (e.g. redventures.com/careers/positions/open?gh_jid=...),
  // which renders it in a cross-origin iframe at
  // job-boards.greenhouse.io/embed/job_app?for=<board slug>&token=<job id>.
  // popup.js probes every frame, so this frame is the one that can capture it.
  // Returns { slug, jobId } for that iframe, else null.
  function greenhouseEmbeddedJob() {
    if (!_GH_PUBLIC_BOARD_HOSTS.has(location.hostname) || location.pathname !== '/embed/job_app') return null;
    const params = new URLSearchParams(location.search);
    const slug = params.get('for');
    const jobId = params.get('token');
    return slug && /^\d+$/.test(jobId || '') ? { slug, jobId } : null;
  }

  // ai: claude-opus-5-5 | 2026-10-27
  // The same embed's board view (job-boards.greenhouse.io/embed/job_board?for=<slug>)
  // links each posting to the company's own page with a gh_jid param, not to a
  // greenhouse.io job path. Returns the board slug in that iframe, else null.
  function greenhouseEmbeddedBoardSlug() {
    if (!_GH_PUBLIC_BOARD_HOSTS.has(location.hostname) || location.pathname !== '/embed/job_board') return null;
    return new URLSearchParams(location.search).get('for') || null;
  }

  // One URL per posting whichever view it was captured from, so they dedup.
  function canonicalGreenhouseJobUrl(slug, jobId) {
    return `https://job-boards.greenhouse.io/${encodeURIComponent(slug)}/jobs/${jobId}`;
  }

  // A link to a Greenhouse posting -> the URL to save it under, or null.
  function greenhouseJobLinkUrl(u) {
    if (u.hostname.includes('greenhouse.io') && _GH_JOB_PATH_RE.test(u.pathname)) return u.origin + u.pathname;
    const boardSlug = greenhouseEmbeddedBoardSlug();
    const jobId = u.searchParams.get('gh_jid');
    return boardSlug && /^\d+$/.test(jobId || '') ? canonicalGreenhouseJobUrl(boardSlug, jobId) : null;
  }

  // ai: claude-sonnet-5 | 2026-08-29
  // Known non-job utility sites (webmail, calendar, docs, chat) that a stray
  // "Send to Job Hunt App" click can hit — the generic smartGeneric/body-text
  // fallback below has no site-shape check, so without this it will happily
  // scrape page chrome (e.g. Gmail's "Search mail" nav heading) as a job title.
  // Checked by exact host or as a subdomain suffix (mail.google.com,
  // inbox.google.com, etc. all end in google.com but google.com itself is left
  // off the list since it's also a legitimate smartGeneric-eligible domain for
  // things like Google-hosted career pages).
  const _NON_JOB_HOSTS = [
    'mail.google.com',
    'calendar.google.com',
    'drive.google.com',
    'docs.google.com',
    'outlook.live.com',
    'outlook.office.com',
    'outlook.office365.com',
    'mail.yahoo.com',
    'slack.com',
    'teams.microsoft.com',
  ];

  function isBlockedHost(host) {
    return _NON_JOB_HOSTS.some(blocked => host === blocked || host.endsWith(`.${blocked}`));
  }

  // ai: claude-sonnet-5 | 2026-10-05
  // Exact-or-subdomain host match, same shape as isBlockedHost's own check
  // above. A plain host.includes('jobgether.com') also matches
  // "notjobgether.com" or "jobgether.com.evil.example" -- low-severity here
  // (worst case is scraping garbage off an unrelated page into the user's
  // own import pipeline, nothing executes), but still worth getting right
  // the same way isBlockedHost already does, with one shared helper instead
  // of each call site repeating its own loose check.
  function isHostOrSubdomain(host, domain) {
    return host === domain || host.endsWith(`.${domain}`);
  }

  // ─── Page Detection ────────────────────────────────────────────────────────

  async function detectPage() {
    const host = location.hostname;
    const path = location.pathname;

    if (isBlockedHost(host)) {
      return { site: host, type: 'blocked', jobCount: 0, description: 'Not a job page — capture blocked' };
    }

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

    // ai: claude-sonnet-5 | 2026-09-22
    // Indeed renders the same split view (job cards on the left, the
    // currently-selected job's full detail on the right) on more than just
    // /jobs — the signed-in homepage ("/", recommendations grouped under
    // "Related to your work experience" etc.) uses the identical widget and
    // DOM shape. Rather than enumerate every path Indeed might mount it on,
    // detect the widget itself: a [data-testid="viewjob-main-content"] detail
    // pane alongside at least one .job_seen_beacon card. Like LinkedIn Search
    // above (and unlike Insight Global's split view further below), the open
    // job's id is tracked directly in the URL query string (?vjk=<key>) once
    // a card is clicked — see indeedJobKey() for the pre-click fallback.
    if (host.includes('indeed.com')) {
      if (/^\/viewjob\/?$/.test(path)) {
        return { site: 'Indeed', type: 'indeedJob', jobCount: 1, description: 'Single job listing' };
      }
      const cardCount = document.querySelectorAll('.job_seen_beacon').length;
      if (cardCount > 0 && document.querySelector('[data-testid="viewjob-main-content"]')) {
        return {
          site: 'Indeed',
          type: 'indeedSplit',
          jobCount: cardCount,
          description: `${cardCount} jobs visible`,
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
    // ai: claude-opus-5-5 | 2026-10-27 -- also the embedded single-job iframe
    // (greenhouseEmbeddedJob), which the board check below used to claim as an
    // empty "search" page, so the capture sent no jobs at all.
    const isGreenhouseHost = host === 'my.greenhouse.io' || _GH_PUBLIC_BOARD_HOSTS.has(host);
    if (
      isGreenhouseHost &&
      (/^\/[^/]+\/jobs\/\d+/.test(path) || /^\/jobs\/[^/]+\/\d+/.test(path) || greenhouseEmbeddedJob())
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
      // ai: claude-opus-5-5 | 2026-10-27 -- via greenhouseJobLinkUrl, so the
      // board embed's gh_jid links count too
      const count = [...document.querySelectorAll('a[href]')].filter(a => {
        try {
          return greenhouseJobLinkUrl(new URL(a.href)) !== null;
        } catch { return false; }
      }).length;
      return { site: 'Greenhouse', type: 'greenhouseSearch', jobCount: count, description: `${count}+ jobs (auto-scrolls to load all)` };
    }

    // ai: gpt-5 | 2026-10-04
    // ai: claude-sonnet-5 | 2026-10-05 -- exact-or-subdomain match (see isHostOrSubdomain)
    if (isHostOrSubdomain(host, 'jobgether.com')) {
      if (/^\/offer\//.test(path)) {
        return { site: 'Jobgether', type: 'jobgetherJob', jobCount: 1, description: 'Single job listing' };
      }
      if (/^\/talent\/matches\/?$/.test(path)) {
        const count = collectJobgetherMatchLinks().length;
        return {
          site: 'Jobgether Matches',
          type: 'jobgetherMatches',
          jobCount: count,
          description: `${count} visible job matches`,
        };
      }
    }

    // ai: claude-sonnet-5 | 2026-10-18
    // Jobanni is a split view like LinkedIn/Indeed/Insight Global/Oracle
    // above -- a result list on the left (10 jobs per RSC page, paginated
    // via a `from=` query param confirmed live by clicking "Next") and the
    // currently-selected job's full detail on the right, auto-opened for the
    // first card on page load. Unlike those DOM-driven split views, the list
    // itself is fetched from the RSC API already (see buildJobanniListPayload),
    // so "list" mode pages through `from` via fetch instead of clicking
    // Next -- same outcome, no DOM pagination needed. See JOBANNI_LIST_CAP
    // for why "list" mode doesn't walk every page for a broad query.
    if (host.includes('jobanni.com') && /^\/search/.test(path)) {
      const total = jobanniTotalHits();
      // ai: claude-sonnet-5 | 2026-10-18
      // jobCount drives the popup's "Send N Jobs to App" button -- report
      // what "All results" will actually fetch (capped by JOBANNI_LIST_CAP),
      // not the raw site total, or the button promises far more than the
      // capture delivers on a broad query (seen live: "1431 jobs" shown, but
      // only 200 would ever be sent).
      const cappedCount = total ? Math.min(total, JOBANNI_LIST_CAP) : 0;
      return {
        site: 'Jobanni',
        type: 'jobanniSplit',
        jobCount: cappedCount,
        description: total
          ? total > JOBANNI_LIST_CAP
            ? `${total} jobs found — captures the first ${JOBANNI_LIST_CAP} via RSC API, or just the job shown on the right`
            : `${total} jobs found — captures all via RSC API, or just the job shown on the right`
          : 'Fetches job data via RSC API (~3s)',
        captureModes: ['list', 'detail'],
      };
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
        const count = (await getAshbyBoardJobs()).length;
        return { site: 'Ashby', type: 'ashbySearch', jobCount: count, description: `${count} jobs` };
      }
    }

    // ai: claude-sonnet-4-6 | 2026-06-20
    if (host.includes('welcometothejungle.com')) {
      // Match canonical /companies/<slug>/jobs/<id> AND email-notification /jobs/<id> links.
      if (/\/companies\/[^/?#]+\/jobs\/[^/?#]{2,}/.test(path) || /^\/jobs\/[^/?#]{2,}/.test(path)) {
        return { site: 'Welcome to the Jungle', type: 'wttjJob', jobCount: 1, description: 'Single job listing' };
      }
      // ai: claude-sonnet-5 | 2026-10-20
      // The dashboard homepage (/, no other path) renders five sections --
      // Saved, Application Started, Applied, Interviewing, Seen -- each with
      // its own [data-testid="preview-card"] list under a shared generic
      // layout. Only "Saved" is jobs the user is still considering; without
      // this branch the page fell through to the generic page-wide capture,
      // which counted every card across all five sections (confirmed live:
      // reported 18 instead of the 4 actually in Saved). Scoping to
      // [data-testid="shortlist-section"] (the Saved section's own wrapper --
      // confirmed via its "Saved" <h3 data-testid="section-title">) is what
      // excludes the other four. React may still be rendering this client-side
      // when detectPage first runs, so poll the same way buildWttjJobPayload
      // does for its own <h1>.
      if (path === '/') {
        const pollDeadline = Date.now() + 5000;
        let savedSection = document.querySelector('[data-testid="shortlist-section"]');
        while (!savedSection && Date.now() < pollDeadline) {
          await sleep(300);
          savedSection = document.querySelector('[data-testid="shortlist-section"]');
        }
        if (savedSection) {
          const count = savedSection.querySelectorAll('[data-testid="preview-card"]').length;
          return { site: 'Welcome to the Jungle', type: 'wttjSaved', jobCount: count, description: `${count} saved jobs` };
        }
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

    // ai: claude-opus-5-5 | 2026-10-29
    // A search on jobs.elastic.co renders its filtered "Search Results" list
    // above the department's full, unfiltered job accordions, so the generic
    // link scan counted every opening in the department (238) instead of the
    // search's own total (23). See elasticSearchResultsList.
    if (host === 'jobs.elastic.co' && elasticSearchResultsList()) {
      const total = elasticSearchTotal();
      return {
        site: 'Elastic',
        type: 'elasticSearch',
        jobCount: total,
        description: `${total} jobs — auto-fetches all pages`,
      };
    }

    // ai: claude-opus-5-5 | 2026-10-28
    // The widgets search API (see phenomRefineSearch) sees the filters ticked
    // on the page, so when it answers its total is the count to report. The
    // older checks below remain for a Phenom site whose API this can't call.
    if (isPhenomSearchPage()) {
      const company = phenomCompany();
      const search = await phenomRefineSearch(0);
      if (!search.problem) {
        return {
          site: company,
          type: 'phenomSearch',
          jobCount: search.totalHits,
          description: `${search.totalHits} jobs — fetches every page and each full description`,
        };
      }
      const total = await phenomTotalHits();
      // ai: claude-sonnet-5 | 2026-10-09
      // See phenomVisibleTotal's comment -- filters applied through Cisco's
      // (or another Phenom site's) UI aren't visible to phenomTotalHits() at
      // all. When the two disagree, report (and later capture) the
      // already-filtered, already-rendered page via the generic DOM-scrape
      // path instead of the wrong, unfiltered total.
      const visibleTotal = phenomVisibleTotal();
      if (visibleTotal != null && visibleTotal !== total) {
        const smartCount = visibleTotal || countSmartGenericJobs();
        return {
          site: company,
          type: 'smartGeneric',
          jobCount: smartCount,
          // ai: claude-opus-5-5 | 2026-10-29 -- names why the full capture isn't available
          description: `${smartCount} jobs detected (filtered — reading the current page only; ${search.problem})`,
        };
      }
      return {
        site: company,
        type: 'phenomSearch',
        jobCount: total,
        description: `${total} jobs — auto-fetches all pages`,
      };
    }

    // ai: claude-sonnet-5 | 2026-08-28
    // Robert Half job pages (roberthalf.com/<locale>/job/<location-slug>/<title-slug>/<id>)
    // embed a schema.org JobPosting JSON-LD block (nested under a WebPage's
    // mainEntity — see readJobPostingLd) with the real title/company/description.
    // DOM scraping is unreliable here: the page also renders a hidden
    // <rhcl-job-card> duplicate (id="joblisting") linking to the same job under
    // a second URL (-usen suffix) whose nearest heading is the site's marketing
    // tagline, not the job title — the generic smartGeneric a[href] scan used
    // to both grab that tagline as the title and double-count the job.
    if (host.includes('roberthalf.com') && /\/job\//.test(path)) {
      return { site: 'Robert Half', type: 'robertHalfJob', jobCount: 1, description: 'Single job listing' };
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

    // ai: claude-opus-5-5 | 2026-10-23
    // Classic iCIMS portals (careers-<company>.icims.com) render the real
    // content inside a same-origin #icims_content_iframe -- the top frame has
    // no job links at all, and the iframe's own links look generic, so both
    // frames used to fall through to the generic types and the capture sent
    // the empty wrapper page as one "Untitled job". See icimsContentDoc().
    // A job page's own frame URL is checked first so any "similar jobs" cards a
    // tenant shows beside the posting can't turn it into a search capture.
    const icimsDoc = icimsContentDoc();
    if (icimsDoc) {
      if (_ICIMS_JOB_PATH_RE.test(icimsDoc.location.pathname)) {
        return { site: icimsCompany(null), type: 'icimsJob', jobCount: 1, description: 'Single job listing' };
      }
      const cards = collectIcimsJobCards(icimsDoc);
      if (cards.length > 0) {
        return {
          site: icimsCompany(null),
          type: 'icimsSearch',
          jobCount: cards.length,
          description: `${cards.length} jobs — fetches full descriptions (~${Math.ceil(cards.length * 0.5)}s)`,
        };
      }
    }

    // ai: claude-sonnet-5 | 2026-08-28
    // Oracle Fusion Recruiting Cloud ("Candidate Experience") career sites —
    // the app path is fixed across tenants even though the hostname is
    // per-company (e.g. Ford's is efds.fa.em5.oraclecloud.com). Clicking a
    // search result's preview arrow opens the job in a modal ON TOP of the
    // still-mounted search-results list (URL becomes .../jobs/preview/<id>/)
    // rather than navigating away from it, so a plain link scan sees every
    // job in the list behind the modal, not just the one open in it — the
    // user has to say which they want, like insightGlobalSplit above.
    const oracleHcmMatch = path.match(
      /\/hcmUI\/CandidateExperience\/[^/]+\/sites\/[^/]+\/(jobs\/preview|job)\/(\d+)/i
    );
    if (oracleHcmMatch) {
      if (oracleHcmMatch[1].toLowerCase() === 'job') {
        return { site: 'Oracle Recruiting Cloud', type: 'oracleHcmJob', jobCount: 1, description: 'Single job listing' };
      }
      const total = oracleHcmTotalHits();
      return {
        site: 'Oracle Recruiting Cloud',
        type: 'oracleHcmSplit',
        jobCount: total,
        description: `${total} jobs found`,
        captureModes: ['detail', 'list'],
      };
    }

    // ai: claude-sonnet-5 | 2026-08-28
    // A job detail page that also lists "similar/related jobs" elsewhere on
    // the page (e.g. Cisco's careers site) makes the link-scanning heuristic
    // below count those recommendations as separate jobs to download, even
    // though there's only one job on the page. If the current URL is itself
    // a job-shaped link AND the page embeds a JobPosting JSON-LD block (see
    // readJobPostingLd) — which a search/listing page's own URL essentially
    // never does, since there's no single canonical job it's "about" — this
    // is unambiguously a single job page. Skip the link-counting heuristic
    // entirely rather than let unrelated recommendations inflate the count.
    let selfUrlForJobPosting;
    try { selfUrlForJobPosting = new URL(location.href); } catch { selfUrlForJobPosting = null; }
    if (selfUrlForJobPosting && isJobLink(selfUrlForJobPosting) && readJobPostingLd(document)) {
      return { site: location.hostname, type: 'html', jobCount: 1, description: 'Single job listing' };
    }

    // Generic fallback: scan for job-shaped links before giving up on counting.
    const smartCount = countSmartGenericJobs();
    if (smartCount >= 2) {
      return { site: location.hostname, type: 'smartGeneric', jobCount: smartCount, description: `${smartCount} jobs detected` };
    }
    return { site: location.hostname, type: 'html', jobCount: 0, description: 'Will send page HTML for parsing' };
  }

  // ai: claude-sonnet-5 | 2026-10-06
  // LinkedIn's 2026-10 search-page redesign (confirmed live) dropped
  // data-job-id/data-occludable-job-id and real <a href="/jobs/view/<id>/">
  // links from the left-rail cards entirely -- each card is now a non-anchor
  // div[role="button"] whose only id is componentkey="job-card-component-ref-<id>".
  // Keep the old attribute/href checks too so this doesn't regress if LinkedIn
  // reverts or A/B-tests the older markup for some accounts.
  function countLinkedInJobs() {
    const ids = new Set();
    document.querySelectorAll('[componentkey^="job-card-component-ref-"]').forEach(el => {
      const m = el.getAttribute('componentkey').match(/job-card-component-ref-(\d+)/);
      if (m) ids.add(m[1]);
    });
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

  // ai: claude-opus-5-5 | 2026-10-14
  // Career sites that filter client-side (e.g. redditinc.com/careers?locations=...
  // &search=...) server-render every opening and just hide non-matching rows, so
  // harvesting every anchor captured the whole unfiltered board (144 vs ~30).
  // When some job links are visible and some aren't, the hidden ones are filtered
  // out — drop them. When none are visible (collapsed accordion, "pick a
  // department" landing view), keep them all rather than capturing nothing.
  // Returns [{ link, u }] pairs so callers don't re-parse the URL. `root`
  // narrows the scan to one part of the page (see buildElasticSearchPayload).
  function collectJobLinkAnchors(root = document) {
    const matches = [];
    root.querySelectorAll('a[href]').forEach(link => {
      let u;
      try { u = new URL(link.href); } catch { return; }
      if (isJobLink(u)) matches.push({ link, u });
    });
    const isShown = el => (el.checkVisibility ? el.checkVisibility() : el.getClientRects().length > 0);
    const visible = matches.filter(m => isShown(m.link));
    return visible.length > 0 ? visible : matches;
  }

  function countSmartGenericJobs() {
    const seen = new Set();
    collectJobLinkAnchors().forEach(({ u }) => seen.add(u.origin + u.pathname));
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

  // ai: claude-sonnet-5 | 2026-10-07
  // Brave's content-script isolated world does not expose JS globals a page
  // sets directly on window (window.__appData here) -- confirmed live: the
  // exact same read returns real data from the page's own "top" console
  // context but undefined from this content script's own context, on the
  // same page, in the same browser. Chrome's isolated world does expose it
  // (this file relied on that for months), so this is a genuine Brave/Chrome
  // difference, not a regression. Content scripts don't have the `scripting`
  // permission, so the read is relayed through background.js, which can use
  // chrome.scripting.executeScript's world:'MAIN' option to read the page's
  // actual JS context directly -- and unlike injecting a <script> tag, that's
  // exempt from the page's CSP, so it works the same on every Chromium
  // browser rather than depending on a given site's CSP being lenient.
  function requestMainWorldValue(key) {
    return chrome.runtime.sendMessage({ action: 'readMainWorldValue', key }).catch(() => null);
  }

  // ai: claude-sonnet-5 | 2026-10-07
  // Ashby reshaped jobBoard.jobPostings since this was written: `isListed` is
  // gone entirely (the array itself now only contains public postings, so that
  // check was silently filtering out every single one) and there's no flat
  // `departmentId`/departmentName on a posting anymore -- only `teamId`, with
  // teams forming a parent/child hierarchy. The `?departmentId=` URL param
  // actually refers to a root team (parentTeamId === null), so matching it
  // means walking a posting's team up to its root, not comparing a field that
  // no longer exists. Confirmed live against jobs.ashbyhq.com/vanta: without
  // this fix every board/filtered-board page reports 0 jobs and the popup
  // falls back to the generic "Send Page to App" button instead of listing
  // individual jobs.
  function resolveAshbyRootTeam(teams, teamId) {
    const byId = new Map(teams.map(t => [t.id, t]));
    let team = byId.get(teamId);
    const seen = new Set();
    while (team?.parentTeamId && !seen.has(team.id)) {
      seen.add(team.id);
      team = byId.get(team.parentTeamId);
    }
    return team;
  }

  async function getAshbyBoardJobs() {
    const appData = await requestMainWorldValue('ashbyAppData');
    const postings = appData?.jobBoard?.jobPostings || [];
    const teams = appData?.jobBoard?.teams || [];
    const params = new URLSearchParams(location.search);
    const departmentId = params.get('departmentId');
    const locationId = params.get('locationId');
    const teamId = params.get('teamId');
    const employmentType = params.get('employmentType');
    const workplaceType = params.get('workplaceType');
    return postings.filter(p => {
      if (departmentId && resolveAshbyRootTeam(teams, p.teamId)?.id !== departmentId) return false;
      if (locationId && p.locationId !== locationId) return false;
      if (teamId && p.teamId !== teamId) return false;
      if (employmentType && p.employmentType !== employmentType) return false;
      if (workplaceType && p.workplaceType !== workplaceType) return false;
      return true;
    });
  }

  // ai: claude-sonnet-5 | 2026-10-12
  // Extracts the JSON object value of `"key": { ... }` from a raw HTML string
  // via brace-matching rather than a regex with a non-greedy DOTALL match --
  // a naive /"posting":\s*(\{.*?\});/s truncates at the first "}" that closes
  // an unrelated nested object (or appears inside a string value), which is
  // common once descriptionHtml is in play. Mirrors
  // backend/scanning/career_site_scanner.py's _json_object_after_key, which
  // fetched job pages can't go through requestMainWorldValue for -- that
  // bridge only reads window.__appData from a tab/frame that's actually
  // loaded, not from HTML fetched separately as text.
  function extractJsonObjectAfterKey(html, key) {
    const keyMatch = new RegExp(`[{,]\\s*"${key}"\\s*:\\s*\\{`).exec(html);
    if (!keyMatch) return null;
    const start = keyMatch.index + keyMatch[0].length - 1; // position of '{'
    let depth = 0, inStr = false, escaped = false, end = -1;
    for (let i = start; i < html.length; i++) {
      const ch = html[i];
      if (inStr) {
        if (escaped) escaped = false;
        else if (ch === '\\') escaped = true;
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') inStr = true;
      else if (ch === '{') depth++;
      else if (ch === '}') {
        depth--;
        if (depth === 0) { end = i + 1; break; }
      }
    }
    if (end === -1) return null;
    try { return JSON.parse(html.slice(start, end)); } catch { return null; }
  }

  // ai: claude-sonnet-5 | 2026-10-12
  // Ashby renamed the posting description field to descriptionHtml at some
  // point after buildAshbyJobPayload was written -- descriptionPlainText and
  // description no longer exist on a live posting (confirmed live against
  // jobs.ashbyhq.com/clickup, 2026-10-12), so every single-job Ashby capture
  // was silently shipping an empty description. Mirrors the already-correct
  // descriptionHtml handling in career_site_scanner.py's _ashby_job_from_posting.
  function ashbyPostingDescription(posting) {
    if (!posting) return '';
    return htmlToText(posting.descriptionHtml || '')
      || posting.descriptionPlainText
      || posting.description
      || '';
  }

  // ai: claude-sonnet-5 | 2026-10-12
  // Board-level jobPostings entries (read by getAshbyBoardJobs) never carry a
  // description at all -- confirmed live: Ashby's board payload only has
  // title/location/team/workplace/employment fields. Fetching each posting's
  // own page is the only way to get real JD content for a board capture.
  // Same-origin fetch: content.js is on jobs.ashbyhq.com when this runs,
  // whether that's a direct board visit or the iframe a company embeds on
  // its own careers page (see detectPage's jobs.ashbyhq.com branch).
  async function fetchAshbyPostingDescription(url) {
    try {
      const res = await fetch(url, { headers: { Accept: 'text/html,application/xhtml+xml' } });
      if (!res.ok) return '';
      const html = await res.text();
      return ashbyPostingDescription(extractJsonObjectAfterKey(html, 'posting'));
    } catch {
      return '';
    }
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

  // ai: claude-sonnet-5 | 2026-09-21
  // Whole-page text fallback for job pages with no structured description. Site chrome
  // and "Similar Jobs" sidebars are excluded: their unrelated titles (e.g. an "Intern"
  // listing beside a senior role) otherwise leak into the JD and trip title-level
  // penalty rules and the fit scorer.
  const NON_JD_LANDMARK_SELECTOR =
    'aside, nav, footer, header, [role="complementary"], [role="navigation"], [role="contentinfo"], [role="banner"]';
  const RELATED_JOBS_HEADING_RE =
    /^(similar|related|recommended|suggested|other|more)\s+(jobs|roles|positions|openings|opportunities)\b/i;

  function collectNonJdElements() {
    const found = new Set();
    document.querySelectorAll(NON_JD_LANDMARK_SELECTOR).forEach(el => {
      // A <header>/<footer> nested in <main>/<article> is part of the posting itself.
      const isPostingChrome = /^(header|footer)$/i.test(el.tagName) && el.closest('main, article');
      if (!isPostingChrome) found.add(el);
    });
    // Related-jobs lists are often plain divs, so find the heading by its text and take
    // everything from that block onward within its container.
    document.body.querySelectorAll('*').forEach(el => {
      if (el.children.length > 0) return;
      const text = (el.textContent || '').trim();
      if (text.length > 40 || !RELATED_JOBS_HEADING_RE.test(text)) return;
      let start = el;
      while (start.parentElement && start.parentElement !== document.body && !start.nextElementSibling) {
        start = start.parentElement;
      }
      for (let node = start; node; node = node.nextElementSibling) found.add(node);
    });
    // Never hide anything that holds the job title.
    return [...found].filter(el => !el.matches('h1') && !el.querySelector('h1'));
  }

  function jobPageBodyText(extraHidden = []) {
    const targets = [...collectNonJdElements(), ...extraHidden];
    const previous = targets.map(el => el.style.display);
    targets.forEach(el => { el.style.display = 'none'; });
    try {
      return document.body.innerText.trim();
    } finally {
      targets.forEach((el, i) => { el.style.display = previous[i]; });
    }
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

  // ai: claude-sonnet-5 | 2026-10-14
  // LinkedIn's "AI-powered job assessment" module (Premium feature --
  // "You'd be a top applicant, based on your skills, experience, and chances
  // of hearing back") only renders on the search-results detail pane and the
  // standalone job page *after* the user clicks "Show match details" -- it's
  // not present on a freshly-loaded standalone /jobs/view/ page, so this only
  // finds something in the split-view capture flow. `.job-details-module` is
  // reused by several unrelated modules (confirmed live), so the match is
  // anchored on the module whose own <h2> is the assessment heading rather
  // than guessing a module-specific class name.
  function linkedInAiInsight() {
    const modules = document.querySelectorAll('.job-details-module');
    for (const mod of modules) {
      const heading = mod.querySelector('h2');
      if (!heading || !/AI-powered job assessment/i.test(heading.innerText || '')) continue;
      const p = mod.querySelector('p');
      if (p && p.innerText.trim()) return p.innerText.trim();
    }
    return '';
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
      lines.push('', jobPageBodyText().slice(0, 12000));
    }

    return JSON.stringify({
      jobs: [{ url, job_title: title, company, listing_text: lines.join('\n'), ai_insight: linkedInAiInsight() }],
    });
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

  // ai: claude-sonnet-5 | 2026-10-14
  // Same 2026-10 redesign as countLinkedInJobs above: title/company classes
  // are now atomic/hashed per build (e.g. "dj9lz8") and unusable as selectors.
  // The positional fallback used to assume a hidden a11y-label line preceded
  // the real title (lines[1]/lines[2]), but that's no longer true (confirmed
  // live 2026-10-14, job 4468808388 "Staff Engineer"/"Cloud Campaign": no
  // leading line, title is lines[0] and company is lines[1] directly) --
  // read title/company from lines[0]/lines[1] once the old classed selectors
  // come up empty, rather than guessing new class names that will just get
  // re-hashed on LinkedIn's next deploy.
  function buildLinkedInSearchPayload() {
    const jobIds = new Set();
    const jobs = [];

    const cardElements = document.querySelectorAll(
      '[componentkey^="job-card-component-ref-"][role="button"], ' +
      '[data-view-name="job-card"], [data-job-id], [data-occludable-job-id], ' +
      '.job-card-list__container, .job-card-container'
    );

    cardElements.forEach(card => {
      const componentKeyId = card.getAttribute('componentkey')?.match(/job-card-component-ref-(\d+)/)?.[1];
      const linkEl = card.querySelector('a[href*="/view/"]');
      const id = componentKeyId ||
                 card.getAttribute('data-job-id') ||
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
      const textLines = card.innerText.split('\n').map(l => l.trim()).filter(Boolean);
      // ai: claude-sonnet-5 | 2026-10-15
      // Collapse immediately-adjacent duplicate lines before indexing -- confirmed
      // live (job 4407130909, "UpSmith"): some cards repeat the title line a
      // second time (likely a hidden a11y label) before the real company line,
      // which made the naive lines[1] fallback read that duplicate instead of
      // the company. Other cards (job 4468808388, "Cloud Campaign") have no
      // such duplicate, so this must collapse rather than always skip line 0.
      const dedupedLines = textLines.filter((line, i) => i === 0 || line !== textLines[i - 1]);
      const title = titleEl?.innerText.trim() || dedupedLines[0] || 'Unknown Title';
      const company = companyEl?.innerText.trim() || dedupedLines[1] || '';
      // ai: claude-sonnet-5 | 2026-10-14
      // The compact per-card AI match badge ("You'd be a top applicant") --
      // confirmed live as a stable, semantically-named class (unlike the
      // hashed title/company classes above).
      const aiInsight = card.querySelector('.job-card-container__job-insight-text')?.innerText.trim() || '';

      jobs.push({
        url,
        job_title: title,
        company,
        listing_text: `${title}\nCompany: ${company}\nURL: ${url}\n\nSnippet:\n${card.innerText.trim()}`,
        ai_insight: aiInsight,
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

  // ai: claude-sonnet-5 | 2026-10-06
  // Independent of the card-level componentkey/innerText extraction above --
  // reads the "X results" / "99+ results" text LinkedIn renders above the
  // list (confirmed live), so a future redesign that breaks the card
  // selectors again doesn't also break this count. Used by listCaptureWarning
  // in buildPayload to detect that failure mode instead of silently
  // capturing a near-empty list again.
  function linkedInStatedResultCount() {
    const m = document.body.innerText.match(/([\d,]+)\+?\s+results?\b/i);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
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
      lines.push('', jobPageBodyText().slice(0, 12000));
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

  // ai: claude-sonnet-5 | 2026-08-26
  // A single hnhiring.com "card" can list several roles for one company as
  // plain-text lines ("Title: https://apply...") with no heading markup per
  // role. When that happens link.innerText is just the auto-linkified URL
  // itself, not a title. Recover the real title from the plain-text node
  // immediately before the link (the "Title: " part of that line) instead.
  const _HN_URL_TEXT_RE = /^https?:\/\//i;

  function _hnPrecedingLineText(link) {
    const prev = link.previousSibling;
    if (!prev || prev.nodeType !== Node.TEXT_NODE) return '';
    return prev.textContent.replace(/[:\-–—]\s*$/, '').trim();
  }

  function _hnTitleFromLink(link) {
    const linkText = link.innerText?.trim() || '';
    return _hnPrecedingLineText(link) || (_HN_URL_TEXT_RE.test(linkText) ? '' : linkText);
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
        const text = _hnTitleFromLink(link) || 'Job Posting';
        jobs.push({ url: href, job_title: text, company: '', listing_text: `${text}\nURL: ${href}` });
        return;
      }

      const headings = [...container.querySelectorAll('h1,h2,h3,h4')];
      const company = headings[0]?.innerText?.split('\n')[0]?.trim() || '';
      const title = headings[1]?.innerText?.split('\n')[0]?.trim() || _hnTitleFromLink(link) || '';
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
    let appData = null;
    while (Date.now() < deadline) {
      appData = await requestMainWorldValue('ashbyAppData');
      if (appData?.posting?.title) break;
      await sleep(300);
    }

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
    const desc = ashbyPostingDescription(posting);

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
  // ai: claude-sonnet-5 | 2026-10-12
  // Board postings never carry a description (see fetchAshbyPostingDescription
  // above) -- every job this used to send was metadata-only (title/company/
  // URL/location) and got rejected server-side for having too little content.
  // Dereference each posting's own page for the real JD instead, same
  // unconditional per-job fetch pattern as buildWellfoundSearchPayload (no
  // opt-in/cap: unlike Kforce/Insight Global's list captures, there is no
  // usable alternative here).
  async function buildAshbySearchPayload() {
    const slug = location.pathname.split('/').filter(Boolean)[0] || '';
    const appData = await requestMainWorldValue('ashbyAppData');
    const company = appData?.organization?.name
      || slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const teams = appData?.jobBoard?.teams || [];
    const teamById = new Map(teams.map(t => [t.id, t]));

    const postings = await getAshbyBoardJobs();
    const total = postings.length;
    const jobs = [];
    for (let i = 0; i < total; i++) {
      const p = postings[i];
      const url = `https://jobs.ashbyhq.com/${slug}/${p.id}`;
      sendProgress(`Fetching description ${i + 1} of ${total}…`, (i + 1) / total);
      const description = await fetchAshbyPostingDescription(url);
      // ai: claude-sonnet-5 | 2026-10-07 -- departmentExternalName/departmentName
      // no longer exist on board postings (see resolveAshbyRootTeam above); the
      // team's own name is the closest available label.
      const department = teamById.get(p.teamId)?.name || '';
      const meta = [department, p.locationExternalName || p.locationName, p.workplaceType, p.employmentType].filter(Boolean);
      const lines = [p.title, 'Company: ' + company, 'URL: ' + url];
      if (meta.length) lines.push(meta.join(' | '));
      if (description) lines.push('', description);
      jobs.push({ url, job_title: p.title, company, listing_text: lines.join('\n') });
      if (i < total - 1) await sleep(300);
    }

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
    // ai: claude-opus-5-5 | 2026-10-27 -- the embed iframe's slug and id come
    // from its query string, and it's saved under the posting's canonical
    // board URL rather than the iframe's (Greenhouse redirects that URL to the
    // company's own page when the company has one).
    const embedded = greenhouseEmbeddedJob();
    const jobUrl = embedded ? canonicalGreenhouseJobUrl(embedded.slug, embedded.jobId) : location.href;
    const pathParts = location.pathname.split('/').filter(Boolean);
    const companySlug = embedded ? embedded.slug : (pathParts[0] === 'jobs' ? pathParts[1] : pathParts[0]) || '';
    let company = companySlug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    const titleAtMatch = document.title.match(/\bat\s+(.+?)(?:\s*[-–|]|$)/i);
    if (titleAtMatch) company = titleAtMatch[1].trim();

    let title = pick(['.job__title h1', 'h1.app-title', 'h1[class*="title"]', 'h1']);
    if (!title) {
      const docTitleMatch = document.title.match(/^(.+?)\s+at\s+/i);
      if (docTitleMatch) title = docTitleMatch[1].trim();
    }
    if (!title) title = document.title.replace(/\s*[-–|].*$/, '').trim();

    // ai: claude-opus-5-5 | 2026-10-27 -- Greenhouse's current board layout
    // (.job__description / .job__location) first: the generic containers
    // below include <main>, which there also wraps the whole application form.
    const description = longestText(['.job__description']) || longestText([
      '#content', '.content', '#job-details', '.job-description',
      '[class*="job-description"]', '[class*="job-details"]',
      'section', 'main',
    ]);
    const jobLocation = pick(['.job__location']);

    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    if (jobLocation) lines.push('Location: ' + jobLocation);
    lines.push('URL: ' + jobUrl);
    if (description) lines.push('', description);

    return JSON.stringify({
      jobs: [{ url: jobUrl, job_title: title, company, listing_text: lines.join('\n') }],
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
        // ai: claude-opus-5-5 | 2026-10-27 -- also the board embed's gh_jid links
        const url = greenhouseJobLinkUrl(u);
        if (!url || seen.has(url)) return;
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

  // ai: claude-sonnet-5 | 2026-10-09
  // Confirmed live on Cisco's careers site (a Phenom People site): clicking a
  // filter chip or "Clear filters" updates the visible job list and "Showing
  // N results" text via client-side JS/AJAX with no full page navigation --
  // but the inline <script> blob this used to read totalHits from is only
  // regenerated by the server on an actual page load, so it can report a
  // stale, pre-filter count (reported live: 1353 jobs offered for download
  // against a page showing 7 filtered results). A fresh same-URL fetch
  // (same query string, same credentials) reflects the server's current
  // knowledge of the session's applied filters, matching what a manual page
  // reload would show. Falls back to the old DOM-scan method if the fetch
  // fails for any reason, so this is never worse than before.
  async function phenomTotalHits() {
    try {
      const res = await fetch(location.pathname + location.search, { credentials: 'include' });
      if (res.ok) {
        const text = await res.text();
        const m = text.match(/"totalHits"\s*:\s*(\d+)/);
        if (m) return parseInt(m[1], 10);
      }
    } catch {
      // fall through to the DOM-scan fallback below
    }
    for (const s of document.querySelectorAll('script:not([src])')) {
      const m = s.textContent.match(/"totalHits"\s*:\s*(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
    return 0;
  }

  // ai: claude-sonnet-5 | 2026-10-09
  // The real fix for the staleness phenomTotalHits() above only partially
  // closes: confirmed live on Cisco's careers site that applying a filter
  // fires a private, session-token-based POST to <host>/widgets (not a GET
  // of the page URL), so neither the live DOM nor a fresh same-URL fetch can
  // see the filtered count -- both still report the unfiltered total (e.g.
  // 459 instead of the page's own "12 results found"). That private API
  // wasn't reverse-engineered further: it's undocumented, returns a bare
  // session-token stub for at least one of its several near-simultaneous
  // calls, and would need re-verification every time Cisco changes it.
  //
  // Cisco's own JS keeps a plain "N results found" line in the page text in
  // sync with whatever filters are currently applied, though -- that's the
  // one reliable signal available without touching the private API. Callers
  // compare it against phenomTotalHits() and fall back to
  // buildSmartGenericPayload() (scrapes the already-filtered, already-
  // rendered page directly, paginating via the real "Next" control) when
  // they disagree, since filters only ever narrow a result set, never widen
  // it.
  function phenomVisibleTotal() {
    const m = document.body.innerText.match(/(\d[\d,]*)\s+results found/i);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
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

  // ai: claude-opus-5-5 | 2026-10-28
  // Cisco-style "<x> - Cisco Careers" titles carry the company name; CVS's
  // ("Search CVS Health Jobs | ...") doesn't, so fall back to the hostname.
  // Captures prefer each job page's JSON-LD hiringOrganization over either.
  function phenomCompany() {
    return (document.title.match(/[-–]\s*(.+?)\s+Careers/i) || [])[1] || companyFromHostname();
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // The phApp bootstrap script (the inline <script> holding csrfToken) also
  // holds the site identity every widgets call has to echo back. Its config
  // comes before the SSR'd job data in that script, so the first match of
  // each key is the config value rather than a field of some job.
  function phenomWidgetsConfig() {
    const script = [...document.querySelectorAll('script:not([src])')]
      .find(s => s.textContent.includes('"csrfToken"'));
    if (!script) return null;
    const read = key => (script.textContent.match(new RegExp(`"${key}"\\s*:\\s*"([^"]*)"`)) || [])[1] || '';
    const config = {
      csrfToken: read('csrfToken'),
      refNum: read('refNum'),
      pageId: read('pageId'),
      siteType: read('siteType'),
      country: read('country'),
      lang: read('locale').toLowerCase(),
      deviceType: read('deviceType') || 'desktop',
    };
    return config.csrfToken && config.refNum ? config : null;
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // Ticked facet checkboxes, in the {field: [values]} shape the widgets API
  // takes. Confirmed live on jobs.cvshealth.com: every facet checkbox carries
  // data-ph-at-facetkey="facet-<field>" and data-ph-at-text="<value>".
  function phenomSelectedFields() {
    const selected = {};
    for (const box of document.querySelectorAll('input[data-ph-at-id="facet-checkbox"]:checked')) {
      const field = (box.getAttribute('data-ph-at-facetkey') || '').replace(/^facet-/, '');
      const value = box.getAttribute('data-ph-at-text');
      if (field && value) (selected[field] ||= []).push(value);
    }
    return selected;
  }

  const PHENOM_PAGE_SIZE = 10; // confirmed from siteConfig "size":"10"

  // ai: claude-opus-5-5 | 2026-10-29
  // The page's own latest search request, as phenomSearchRecorder.js (in the
  // page's JS world) recorded it -- null until the page has sent one (a
  // search that was only loaded, never re-filtered), or if the recorder
  // wasn't there yet (the extension was reloaded after the page was).
  function recordedPhenomSearch() {
    try {
      const recorded = JSON.parse(document.documentElement.getAttribute('data-jha-phenom-search') || 'null');
      return recorded?.body && typeof recorded.body === 'object' ? recorded : null;
    } catch {
      return null;
    }
  }

  // ai: claude-opus-5-5 | 2026-10-29
  // The request body for one page of the search on screen. Replaying the
  // page's own recorded request is exact; rebuilding it from the facet
  // checkboxes misses what they don't show -- a CVS capture rebuilt that way
  // disagreed with the page's count (2026-10-29), and the page's own request
  // turned out to carry a 50-mile locationData radius among other fields.
  function phenomSearchRequest(from) {
    const recorded = recordedPhenomSearch();
    const config = phenomWidgetsConfig();
    const csrfToken = recorded?.csrfToken || config?.csrfToken;
    if (recorded && csrfToken) {
      return {
        csrfToken,
        body: { ...recorded.body, ddoKey: 'refineSearch', from, size: PHENOM_PAGE_SIZE, jobs: true, counts: false },
      };
    }
    if (!config) return null;
    const { csrfToken: configToken, ...site } = config;
    return {
      csrfToken: configToken,
      body: {
        ...site,
        pageName: 'search-results',
        ddoKey: 'refineSearch',
        keywords: new URLSearchParams(location.search).get('keywords') || '',
        selected_fields: phenomSelectedFields(),
        locationData: {}, // without it the API answers with zero hits
        from,
        size: PHENOM_PAGE_SIZE,
        jobs: true,
        counts: false,
        global: true,
        s: '1',
      },
    };
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // One page of the search the user is looking at, via the same POST
  // <origin>/widgets call Phenom's own JS makes when a filter is ticked.
  // Worked out live on jobs.cvshealth.com after both older routes failed
  // there: ticking a filter changes the list without changing the URL, and
  // a fetch() of a "?from=10&s=1" page URL drops even the keywords (19,749
  // results instead of 194) because the server only applies its stored
  // search on a real navigation.
  // ai: claude-opus-5-5 | 2026-10-29 -- returns {problem} instead of null when
  // the page lacks the config, the call fails, or the total disagrees with
  // the page's own "N results found", so the popup can say why it fell back
  // (callers then use the older routes, so this is never worse than before).
  async function phenomRefineSearch(from) {
    const request = phenomSearchRequest(from);
    if (!request) return { problem: 'no search config on the page' };
    let result;
    try {
      const res = await fetch(location.origin + '/widgets', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': request.csrfToken },
        body: JSON.stringify(request.body),
      });
      if (!res.ok) return { problem: `search API answered HTTP ${res.status}` };
      result = (await res.json())?.refineSearch;
    } catch (err) {
      return { problem: `search API call failed (${err?.message || err})` };
    }
    const jobs = result?.data?.jobs;
    if (!Array.isArray(jobs) || typeof result.totalHits !== 'number') {
      return { problem: 'search API answered without a job list' };
    }
    const visibleTotal = phenomVisibleTotal();
    if (visibleTotal != null && visibleTotal !== result.totalHits) {
      return { problem: `search API found ${result.totalHits}, the page shows ${visibleTotal}` };
    }
    return { totalHits: result.totalHits, jobs };
  }

  // ai: claude-opus-5-5 | 2026-10-29
  // The DOM-scrape fallback for a Phenom search can't page past what's on
  // screen when Next is a real link (jobs.cvshealth.com's is), so a capture
  // short of the page's count must say so instead of importing 10 of 52.
  function phenomFallbackWarning(jobsJson) {
    const expected = phenomVisibleTotal();
    let captured;
    try { captured = JSON.parse(jobsJson).jobs.length; } catch { return null; }
    if (expected == null || captured >= expected) return null;
    return `Captured only ${captured} of the ${expected} jobs this search shows. Reload this page and try again; if it still falls short, the site's search has changed.`;
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // Search results only carry a ~200-character descriptionTeaser, which the
  // app discards as too short to score. Each job page has the full posting
  // in a JobPosting JSON-LD block (9.5k characters on the CVS jobs checked).
  async function fetchPhenomPosting(jobUrl) {
    try {
      const res = await fetch(jobUrl, { credentials: 'include', headers: { Accept: 'text/html,application/xhtml+xml' } });
      if (!res.ok) return null;
      return readJobPostingLd(new DOMParser().parseFromString(await res.text(), 'text/html'));
    } catch {
      return null;
    }
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // Shared by both capture routes below. posting is the job page's JSON-LD
  // (null when it wasn't fetched or had none -- the teaser stands in then).
  function phenomJobEntry(job, fallbackCompany, jobBase, posting) {
    const title = job.title || '';
    const slug = title.toLowerCase()
      .replace(/[–—]/g, '-').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    const url = `${jobBase}/job/${job.jobId}/${slug}`;
    const company = posting?.hiringOrganization?.name || fallbackCompany;
    const locs = (job.multi_location?.length ? job.multi_location : [job.location])
      .filter(Boolean).join('; ');
    // Cisco names the work-arrangement field RemoteType; CVS names it remote.
    const workEnvironment = job.RemoteType || job.remote;
    const description = posting?.description ? htmlToText(posting.description) : job.descriptionTeaser;

    const lines = [
      title,
      `Company: ${company}`,
      `URL: ${url}`,
      locs && `Location: ${locs}`,
      workEnvironment && `Work Environment: ${workEnvironment}`,
      description && `\n${description}`,
    ].filter(Boolean);

    return { url, job_title: title, company, listing_text: lines.join('\n') };
  }

  // ai: claude-opus-5-5 | 2026-10-28
  // Every job in the search the user is looking at (filters included), each
  // with its full description from its own page.
  async function buildPhenomSearchPayload() {
    const company = phenomCompany();
    // Strip "/search-results…" to get the locale prefix for job detail URLs
    const jobBase = location.origin + location.pathname.replace(/\/search-results.*$/, '');

    const first = await phenomRefineSearch(0);
    if (first.problem) return buildPhenomSearchPayloadFromPageUrls(company, jobBase);

    const seen = new Set();
    const listed = [];
    const addJobs = jobs => jobs.forEach(job => {
      if (seen.has(job.jobId)) return;
      seen.add(job.jobId);
      listed.push(job);
    });
    addJobs(first.jobs);

    const totalPages = Math.ceil(first.totalHits / PHENOM_PAGE_SIZE);
    for (let page = 1; page < totalPages; page++) {
      sendProgress(`Listing jobs... page ${page + 1} of ${totalPages}`, undefined);
      await sleep(400);
      const next = await phenomRefineSearch(page * PHENOM_PAGE_SIZE);
      if (next.problem) break;
      addJobs(next.jobs);
    }

    const allJobs = [];
    for (let i = 0; i < listed.length; i++) {
      sendProgress(`Fetching job details... (${i + 1}/${listed.length})`, (i + 1) / listed.length);
      const listingOnly = phenomJobEntry(listed[i], company, jobBase, null);
      const posting = await fetchPhenomPosting(listingOnly.url);
      allJobs.push(posting ? phenomJobEntry(listed[i], company, jobBase, posting) : listingOnly);
      if (i < listed.length - 1) await sleep(400);
    }

    return allJobs.length
      ? JSON.stringify({ jobs: allJobs, source: { url: location.href } })
      : buildHtmlPayload();
  }

  // ai: claude-sonnet-4-6 | 2026-06-17
  // Fetches every page of a Phenom People search-results page and aggregates jobs.
  // Page data is SSR'd into phApp.ddo.eagerLoadRefineSearch inside an inline <script>;
  // we read it from the raw HTML of each fetched page without needing eval or injection.
  // ai: claude-opus-5-5 | 2026-10-28 — now only the fallback for a Phenom
  // site whose widgets API phenomRefineSearch() can't call.
  async function buildPhenomSearchPayloadFromPageUrls(company, jobBase) {
    const totalHits = await phenomTotalHits();
    // ai: claude-sonnet-5 | 2026-10-09
    // Same disagreement check detectPage() already made for the preview
    // badge -- repeated here in case filters changed in the gap between that
    // classification and the user clicking capture, or some other path
    // calls this directly. See phenomVisibleTotal's comment for why this
    // check exists at all rather than just trusting totalHits.
    const visibleTotal = phenomVisibleTotal();
    if (visibleTotal != null && visibleTotal !== totalHits) {
      return buildSmartGenericPayload();
    }
    if (!totalHits) return buildHtmlPayload();

    // Canonical base URL without pagination params
    const baseUrl = new URL(location.href);
    baseUrl.searchParams.delete('from');
    baseUrl.searchParams.delete('s');

    const totalPages = Math.ceil(totalHits / PHENOM_PAGE_SIZE);
    const seen = new Set();
    const allJobs = [];

    for (let page = 0; page < totalPages; page++) {
      const from = page * PHENOM_PAGE_SIZE;
      sendProgress(
        `Fetching page ${page + 1} of ${totalPages}…`,
        Math.min(from + PHENOM_PAGE_SIZE, totalHits) / totalHits
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
        allJobs.push(phenomJobEntry(job, company, jobBase, null));
      }

      if (from + PHENOM_PAGE_SIZE < totalHits) await sleep(400);
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

  // ai: claude-opus-5-5 | 2026-10-29
  // jobs.elastic.co (Elastic Search UI on MUI) uses ul.sui-results-container
  // both for a search's own results and for every department accordion under
  // them; only the search's list sits outside an accordion region. Plain
  // department pages (no search) have no such list and stay on the generic path.
  function elasticSearchResultsList() {
    return [...document.querySelectorAll('ul.sui-results-container')]
      .find(list => !list.closest('.MuiAccordion-region')) || null;
  }

  // "23 Results" -- the search's total across every results page.
  function elasticSearchTotal() {
    const countEl = [...document.querySelectorAll('p, span, h2, h3')]
      .find(el => /^\s*[\d,]+\s+results?\s*$/i.test(el.textContent || ''));
    if (countEl) return parseInt(countEl.textContent.replace(/[^\d]/g, ''), 10);
    return elasticSearchResultsList()?.querySelectorAll('li.sui-result').length || 0;
  }

  // The results are paginated client-side (Next swaps the list in place, no
  // navigation), so the generic Next-clicking harvester covers every page once
  // it's scoped to the results list. It only pages forward, so start from page
  // 1 when the user has already paged ahead.
  async function buildElasticSearchPayload() {
    const firstPage = document.querySelector('nav[aria-label="Pagination"] button[aria-label="Go to page 1"]');
    if (firstPage && !firstPage.disabled) {
      firstPage.click();
      await sleep(1200);
    }
    return buildSmartGenericPayload({ getLinkRoot: elasticSearchResultsList, company: 'Elastic' });
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
  // ai: claude-sonnet-5 | 2026-08-28 — also unwrap a JobPosting nested under a
  // WebPage's `mainEntity` (Robert Half's shape) rather than at the top level.
  function readJobPostingLd(doc) {
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(s.textContent || '');
        const items = Array.isArray(data) ? data : (data['@graph'] || [data]);
        for (const item of items) {
          if (item && item['@type'] === 'JobPosting') return item;
          if (item?.mainEntity?.['@type'] === 'JobPosting') return item.mainEntity;
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
  // ai: claude-sonnet-5 | 2026-08-28 — renamed from jibeJobLines: now shared
  // with buildRobertHalfJobPayload, which also builds listing text from a
  // JSON-LD JobPosting block.
  function jobPostingLines({ title, company, reqId, location: jobLocation, url, description, fallbackText }) {
    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    if (reqId) lines.push('Req ID: ' + reqId);
    if (jobLocation) lines.push('Location: ' + jobLocation); // ai: claude-opus-5-5 | 2026-10-23
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
      : jobPageBodyText().slice(0, 12000);
    const url = location.origin + location.pathname;

    return JSON.stringify({
      jobs: [{
        url,
        job_title: title,
        company,
        listing_text: jobPostingLines({ title, company, reqId, url, description }),
      }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-08-28
  // Single Robert Half job page — prefer the JSON-LD JobPosting block (see
  // detectPage's roberthalf.com branch for why: the DOM also contains a
  // hidden duplicate job card whose nearest heading is the site tagline, not
  // the job title). reqId falls back to the trailing id segment of the URL
  // (e.g. "03230-0013488334"), since this posting shape has no `identifier`.
  function buildRobertHalfJobPayload() {
    const posting = readJobPostingLd(document);
    const title = posting?.title || pick(['#jobdetailsheading', 'h1']);
    const company = posting?.hiringOrganization?.name || 'Robert Half';
    const reqId = posting?.identifier?.value
      || (location.pathname.match(/\/job\/[^/]+\/[^/]+\/([\w-]+)\/?$/) || [])[1]
      || '';
    const description = posting?.description
      ? htmlToText(posting.description)
      : jobPageBodyText().slice(0, 12000);
    const url = location.origin + location.pathname;

    return JSON.stringify({
      jobs: [{
        url,
        job_title: title,
        company,
        listing_text: jobPostingLines({ title, company, reqId, url, description }),
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
        listing_text: jobPostingLines({ title, company, reqId, url: item.url, description, fallbackText: item.cardText }),
      });
      if (i < items.length - 1) await sleep(400);
    }

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ─── iCIMS classic portals (careers-<company>.icims.com) ───────────────────

  // ai: claude-opus-5-5 | 2026-10-23
  // The document holding the iCIMS job content: this frame's own document when
  // it is the content frame (the popup also probes every frame, which covers a
  // company embedding iCIMS cross-origin on its own domain), otherwise the
  // same-origin #icims_content_iframe of an icims.com wrapper page. Reading the
  // iframe from the top frame keeps the context-menu capture working too, since
  // that flow only ever messages frame 0.
  function icimsContentDoc() {
    if (document.querySelector('.iCIMS_JobsTable, .iCIMS_JobContent')) return document;
    try {
      const inner = document.querySelector('#icims_content_iframe')?.contentDocument;
      if (inner?.querySelector('.iCIMS_JobsTable, .iCIMS_JobContent')) return inner;
    } catch { /* cross-origin iframe -- the popup's per-frame probe handles it */ }
    return null;
  }

  // ai: claude-opus-5-5 | 2026-10-23
  // companyFromHostname() only strips "careers." -- iCIMS tenants use
  // "careers-<company>.icims.com", which it would turn into "Careers Cotiviti".
  function icimsCompany(posting) {
    if (posting?.hiringOrganization?.name) return posting.hiringOrganization.name;
    const m = location.hostname.match(/^(?:careers|jobs)-(.+?)\.icims\.com$/i);
    if (!m) return companyFromHostname();
    return m[1].replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  // ai: claude-opus-5-5 | 2026-10-23
  // Text of a card field whose value follows an sr-only "<label>" span
  // (e.g. "Job Locations" → "US-Remote", "ID" → "2026-20757").
  function icimsCardField(card, label) {
    for (const lbl of card.querySelectorAll('.field-label')) {
      if (lbl.textContent.trim().toLowerCase() !== label.toLowerCase()) continue;
      const text = (lbl.parentElement?.textContent || '').replace(lbl.textContent, '').trim();
      if (text) return text.replace(/\s+/g, ' ');
    }
    return '';
  }

  // ai: claude-opus-5-5 | 2026-10-23
  // Job links look like /jobs/<id>/<slug>/job?in_iframe=1. The "Welcome page" /
  // "Log in" nav anchors share the iCIMS_Anchor class, hence the path check.
  const _ICIMS_JOB_PATH_RE = /^\/jobs\/(\d+)\/[^/]+\/job\/?$/;

  // ai: claude-opus-5-5 | 2026-10-23
  function collectIcimsJobCards(doc) {
    const seen = new Map(); // numeric job id → card info
    doc.querySelectorAll('.iCIMS_JobsTable a.iCIMS_Anchor[href]').forEach(a => {
      let u;
      try { u = new URL(a.href); } catch { return; }
      const m = u.pathname.match(_ICIMS_JOB_PATH_RE);
      if (!m || seen.has(m[1])) return;
      const card = a.closest('.iCIMS_JobCardItem, .row') || a;
      const title = (card.querySelector('h3')?.textContent || a.textContent || '')
        .replace(/^\s*Title\s*/i, '').trim();
      seen.set(m[1], {
        url: u.origin + u.pathname,
        title,
        reqId: icimsCardField(card, 'ID'),
        location: icimsCardField(card, 'Job Locations'),
        cardText: (card.innerText || card.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 500),
      });
    });
    return [...seen.values()];
  }

  // ai: claude-opus-5-5 | 2026-10-23
  // Only the in_iframe=1 variant of a job page carries the JobPosting JSON-LD
  // (confirmed live on careers-cotiviti.icims.com) -- the bare URL returns the
  // wrapper page, which has none.
  async function buildIcimsSearchPayload() {
    const items = collectIcimsJobCards(icimsContentDoc());
    const jobs = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      sendProgress(`Fetching job details... (${i + 1}/${items.length})`, (i + 1) / items.length);

      let posting = null;
      try {
        const res = await fetch(item.url + '?in_iframe=1', { headers: { Accept: 'text/html,application/xhtml+xml' } });
        if (res.ok) posting = readJobPostingLd(new DOMParser().parseFromString(await res.text(), 'text/html'));
      } catch { /* keep card-level data */ }

      const title = posting?.title || item.title;
      const company = icimsCompany(posting);
      const description = posting?.description ? htmlToText(posting.description) : '';

      jobs.push({
        url: item.url,
        job_title: title,
        company,
        listing_text: jobPostingLines({
          title, company, reqId: item.reqId, location: item.location, url: item.url,
          description, fallbackText: item.cardText,
        }),
      });
      if (i < items.length - 1) await sleep(400);
    }

    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ai: claude-opus-5-5 | 2026-10-23
  function buildIcimsJobPayload() {
    const doc = icimsContentDoc();
    const posting = readJobPostingLd(doc);
    const title = posting?.title || doc.querySelector('h1')?.textContent.trim() || '';
    const company = icimsCompany(posting);
    const description = posting?.description
      ? htmlToText(posting.description)
      : (doc.querySelector('.iCIMS_JobContent')?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 12000);
    const url = location.origin + doc.location.pathname;

    return JSON.stringify({
      jobs: [{
        url,
        job_title: title,
        company,
        listing_text: jobPostingLines({ title, company, url, description }),
      }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-10-18
  // Jobanni aggregates the whole remote job market -- a broad query's total
  // (seen live: 4584 for "software engineer") is nothing like a single
  // company's ATS board, where the other "auto-fetches all pages" sites
  // above (Phenom, Kforce, Insight Global) top out in the low hundreds.
  // Bounds how many RSC pages buildJobanniListPayload will walk so "All
  // results" finishes in a reasonable time instead of serially fetching
  // thousands of rows.
  const JOBANNI_LIST_CAP = 200;

  function jobanniTotalHits() {
    const m = document.body.innerText.match(/([\d,]+)\s+jobs?\s+found/i);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : 0;
  }

  // ai: claude-sonnet-4-6 | 2026-06-20
  // Jobanni uses Next.js App Router with RSC.  The search results page streams
  // job data (including companyName) via the RSC navigation endpoint, exactly
  // as the server-side scanner does.  Fetching that endpoint from the content
  // script (same origin → no CORS) gives us accurate company names that the
  // generic DOM-walk cannot reliably extract.
  async function jobanniBuildHash() {
    // Extract the Next.js build hash — it appears in _buildManifest.js script URLs.
    let buildHash = null;
    document.querySelectorAll('script[src*="/_next/static/"]').forEach(s => {
      const m = (s.src || '').match(/\/static\/([^/]+)\/_buildManifest/);
      if (m) buildHash = m[1];
    });
    if (!buildHash) {
      // ai: claude-sonnet-5 | 2026-10-07 -- see requestMainWorldValue above;
      // window.__NEXT_DATA__ has the same Brave isolated-world gap as Ashby's
      // window.__appData, just for a fallback path instead of the main one.
      const nextData = await requestMainWorldValue('jobanniNextData');
      buildHash = nextData?.buildId || null;
    }
    return buildHash;
  }

  // Parses one RSC response into {total, jobs}, or null if the stream didn't
  // have the expected {"total":N,"jobs":[...]} shape (site layout changed,
  // or this page/offset genuinely has nothing — caller decides which).
  function jobanniParseRscJobs(rscText) {
    // Locate the {"total":N,"jobs":[...]} object in the RSC stream.
    const jobsStart = rscText.indexOf('{"total":');
    if (jobsStart === -1) return null;

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
    if (jobsEnd === -1) return null;

    let jobsData;
    try {
      jobsData = JSON.parse(rscText.slice(jobsStart, jobsEnd));
    } catch {
      return null;
    }

    const rawJobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];

    // Resolve "$ref" description pointers to their T-format text blocks.
    // The blocks appear in the RSC stream after the jobs object: {ref}:T{hex_len},{text}
    const afterJobs = rscText.slice(jobsEnd);
    const descMap = {};
    rawJobs.forEach(job => {
      if (typeof job.description !== 'string' || !job.description.startsWith('$')) return;
      const ref = job.description.slice(1);
      const escaped = ref.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      // Lookbehind anchor so ref "5" can't match inside an unrelated longer
      // id like "a5:T...,"  -- an unanchored search picks whichever occurs
      // first in the stream, silently pairing one job's title/company with a
      // different job's description text (job 6595, found 2026-10-19: title
      // and company stayed correct since those come straight off the JSON,
      // but the JD body got mismatched).
      // ai: claude-sonnet-5 | 2026-10-19
      const m = afterJobs.match(new RegExp('(?<![0-9a-zA-Z])' + escaped + ':T([0-9a-f]+),'));
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

    return { total: typeof jobsData.total === 'number' ? jobsData.total : null, jobs };
  }

  // ai: claude-sonnet-5 | 2026-10-18
  // "All results" mode: pages through the RSC endpoint, incrementing the
  // `from=` query param by however many jobs the previous page returned (the
  // same param Jobanni's own "Next" link updates -- confirmed live) until the
  // reported total is reached or JOBANNI_LIST_CAP is hit.
  async function buildJobanniListPayload() {
    sendProgress('Connecting to Jobanni RSC API…', 5);

    // ai: claude-sonnet-5 | 2026-10-18
    // buildHash is best-effort, not required: confirmed live that Jobanni's
    // RSC endpoint returns correctly paginated data across multiple `from=`
    // offsets even with no `_rsc` value supplied at all -- it appears to be
    // a client-side cache key the server doesn't actually validate. The
    // single-page fetch this replaced bailed out to buildSmartGenericPayload()
    // (a DOM-only scrape of whatever ~10 cards are currently rendered)
    // whenever the hash lookup failed, which it reliably does on this page:
    // the `/_next/static/` script-tag selector finds nothing, and the
    // window.__NEXT_DATA__ fallback is a Pages Router global that Jobanni's
    // App Router build doesn't set. That silent fallback was undetectable
    // back when jobCount was always reported as 0, but once "All results"
    // started promising a specific capped count, the mismatch between the
    // promised count and the fallback's ~10-job reality became visible.
    const buildHash = await jobanniBuildHash();

    const parsed = new URL(location.href);
    const baseParams = new URLSearchParams(parsed.search);
    baseParams.delete('from');

    const allJobs = [];
    let total = null;
    let offset = 0;

    while (allJobs.length < JOBANNI_LIST_CAP) {
      const params = new URLSearchParams(baseParams);
      if (offset > 0) params.set('from', String(offset));
      const navSearch = `?${params}`;
      const rscParams = new URLSearchParams(params);
      if (buildHash) rscParams.set('_rsc', buildHash);
      const rscUrl = `${parsed.origin}${parsed.pathname}?${rscParams}`;

      let rscText;
      try {
        const res = await fetch(rscUrl, {
          headers: { 'RSC': '1', 'Next-Url': parsed.pathname + navSearch },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        rscText = await res.text();
      } catch {
        break; // stop paging on a fetch error; keep whatever was already captured
      }

      const page = jobanniParseRscJobs(rscText);
      if (!page || !page.jobs.length) break;
      if (total == null) total = page.total;

      allJobs.push(...page.jobs);
      const expected = Math.min(total || JOBANNI_LIST_CAP, JOBANNI_LIST_CAP);
      sendProgress(`Fetched ${allJobs.length} of ${expected} jobs…`, Math.min(90, 10 + 80 * allJobs.length / expected));

      offset += page.jobs.length;
      if (total != null && allJobs.length >= total) break;
    }

    if (!allJobs.length) return buildSmartGenericPayload();

    const jobs = allJobs.slice(0, JOBANNI_LIST_CAP);
    sendProgress(`Sending ${jobs.length} jobs…`, 95);
    return JSON.stringify({
      jobs,
      source: { url: location.href, name: 'Jobanni', platform: 'jobanni' },
    });
  }

  // ai: claude-sonnet-5 | 2026-10-18
  // "This job only" mode: Jobanni's search page auto-opens the first result
  // in a right-hand <article> detail pane (and re-renders it in place when a
  // different card is clicked, no navigation) -- same split-view shape as
  // Insight Global/Oracle above. The article's only two children are a fixed
  // header block (title/company/location/remote badge) and the description
  // body; stripping the header out of a clone before reading innerText avoids
  // duplicating the title/company into listing_text (confirmed live: article
  // has exactly 2 children, header class "pb-3 flex flex-col gap-1 not-prose").
  function extractJobanniDetail() {
    const article = document.querySelector('article');
    const title = article?.querySelector('h1')?.textContent.trim() || '';
    const company = article?.querySelector('h3')?.textContent.trim() || '';
    const badges = article
      ? [...article.querySelectorAll('.text-muted-foreground')].map(el => el.textContent.trim()).filter(Boolean)
      : [];
    const jobLocation = badges[0] || '';
    const jobType = badges[1] || '';

    let description = '';
    if (article) {
      const clone = article.cloneNode(true);
      clone.querySelector('.pb-3')?.remove();
      description = clone.innerText.trim();
    }

    const shareLink = [...document.querySelectorAll('a')]
      .find(a => a.textContent.trim() === 'Share this page');
    const url = shareLink?.href || location.href;

    return { title, company, jobLocation, jobType, description, url };
  }

  function buildJobanniDetailPayload() {
    const { title, company, jobLocation, jobType, description, url } = extractJobanniDetail();
    const listing_text = [
      title,
      company && `Company: ${company}`,
      `URL: ${url}`,
      jobLocation && `Location: ${jobLocation}`,
      jobType && `Type: ${jobType}`,
      description && `\n${description}`,
    ].filter(Boolean).join('\n');
    return JSON.stringify({
      jobs: [{ url, job_title: title, company, listing_text, platform: 'Jobanni' }],
      source: { url: location.href, name: 'Jobanni', platform: 'jobanni' },
    });
  }

  // ai: claude-sonnet-5 | 2026-09-29
  // Generic click-based "Next page" pagination for AJAX-paginated job boards --
  // sites whose Next control swaps in new results without a full page
  // navigation. A real navigation would tear down this content-script
  // execution context mid-loop, so those sites are out of scope here, the
  // same limitation buildGreenhouseSearchPayload's scroll loop already has.
  // rel="next" and an explicit aria-label are far more reliable than text
  // matching (a "Read more" job-description toggle could otherwise match),
  // so they're tried first; the text fallback requires an exact match, not
  // a substring, to avoid misfiring on unrelated page chrome.
  const _NEXT_PAGE_TEXT_RE = /^(?:next|next page|show more|load more|more results|»|›)$/i;

  function isElementVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  }

  function isNextControlEnabled(el) {
    return !el.disabled && el.getAttribute('aria-disabled') !== 'true'
      && !el.className?.toString().toLowerCase().includes('disabled');
  }

  // ai: claude-sonnet-5 | 2026-09-29
  // An <a> whose href resolves to a different document (cross-origin, or a
  // different path/query on this origin) triggers a real page navigation on
  // click, which unloads this content script before buildSmartGenericPayload
  // ever resolves -- rel="next" in particular is commonly used for ordinary
  // paginated navigation, not AJAX. A hash-only or javascript:/empty href
  // can't navigate away, so those are still safe to click.
  function isNavigatingAnchor(el) {
    if (el.tagName !== 'A') return false;
    const href = el.getAttribute('href');
    if (href == null) return false;
    const trimmed = href.trim();
    if (trimmed === '' || trimmed === '#' || /^javascript:/i.test(trimmed)) return false;
    let target;
    try {
      target = new URL(href, location.href);
    } catch {
      return true;
    }
    if (target.origin !== location.origin) return true;
    return target.pathname !== location.pathname || target.search !== location.search;
  }

  function isSafeToClick(el) {
    return isElementVisible(el) && isNextControlEnabled(el) && !isNavigatingAnchor(el);
  }

  function findNextPageControl() {
    const relNext = document.querySelector('a[rel="next"]');
    if (relNext && isSafeToClick(relNext)) return relNext;

    // ai: claude-opus-5-5 | 2026-10-28
    // Check every match, not just the first: jobs.cvshealth.com puts hidden
    // carousel arrows (aria-label="view next slide") ahead of the real
    // pagination control, and stopping at the first one found no Next at all.
    const ariaNext = [...document.querySelectorAll(
      'a[aria-label="Next"], a[aria-label*="next" i], button[aria-label="Next"], button[aria-label*="next" i]'
    )].find(isSafeToClick);
    if (ariaNext) return ariaNext;

    for (const el of document.querySelectorAll('a, button')) {
      const text = (el.innerText || el.textContent || '').trim();
      if (_NEXT_PAGE_TEXT_RE.test(text) && isSafeToClick(el)) {
        return el;
      }
    }
    return null;
  }

  const _SMART_GENERIC_MAX_PAGES = 20;

  // ai: claude-opus-5-5 | 2026-10-29
  // A Next click that fetches its results from the server (jobs.elastic.co's
  // /api/appSearch) can take longer than any fixed delay; a fixed 1.2s sleep
  // read the unchanged page 1, saw no new jobs, and stopped paging at 20 of 23.
  // Wait for the links themselves to change instead, capped so a click that
  // loads nothing still ends the loop.
  const _NEXT_PAGE_WAIT_MS = 10000;

  function linkSignature(root) {
    if (!root) return '';
    return [...root.querySelectorAll('a[href]')].map(a => a.href).join('\n');
  }

  async function waitForLinksToChange(getLinkRoot, prevSignature) {
    const deadline = Date.now() + _NEXT_PAGE_WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(250);
      if (linkSignature(getLinkRoot()) !== prevSignature) {
        await sleep(250); // let the rest of the list finish rendering
        return;
      }
    }
  }

  // Generic job-link harvester — works on Phenom People (Cisco), Workday, Lever,
  // and any other career-site structure the specialised builders don't cover.
  // ai: claude-opus-5-5 | 2026-10-29
  // getLinkRoot, when given, returns the element to harvest from. It's a getter
  // rather than an element because a React results list can be replaced
  // wholesale when the next page renders. company, when the caller already
  // knows it, saves the backend an LLM call to guess it from each card.
  async function buildSmartGenericPayload({ getLinkRoot = () => document, company = '' } = {}) {
    const seen = new Set();
    const jobs = [];

    function collectJobLinks() {
      const root = getLinkRoot();
      if (!root) return;
      collectJobLinkAnchors(root).forEach(({ link, u }) => { // ai: claude-opus-5-5 | 2026-10-14
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

        jobs.push({ url: link.href, job_title: title, company, listing_text: lines.join('\n') });
      });
    }

    collectJobLinks();

    // ai: claude-sonnet-5 | 2026-09-29
    // Click through additional pages until a click produces no new jobs
    // (mirrors buildGreenhouseSearchPayload's scroll-stall pattern), no
    // Next control is found, or the page cap is hit.
    for (let page = 1; page < _SMART_GENERIC_MAX_PAGES; page++) {
      const nextControl = findNextPageControl();
      if (!nextControl) break;

      const prevCount = jobs.length;
      const prevLinks = linkSignature(getLinkRoot());
      sendProgress(`Loading more jobs... (${jobs.length} found)`, undefined);
      nextControl.click();
      await waitForLinksToChange(getLinkRoot, prevLinks); // ai: claude-opus-5-5 | 2026-10-29 -- was a fixed sleep(1200)
      collectJobLinks();
      if (jobs.length === prevCount) break;
    }

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
        // ai: claude-sonnet-5 | 2026-08-28
        // Prefer a schema.org JobPosting block (see readJobPostingLd) over DOM
        // heading scraping when the page has one — the RobertHalf bug (a
        // hidden duplicate card whose nearest heading was the site tagline,
        // not the job title) is a DOM-scraping failure mode that can happen
        // on any AEM-style or templated career site landing in this generic
        // fallback, not just RobertHalf. JSON-LD sidesteps it entirely.
        const posting = readJobPostingLd(document);
        const title = posting?.title
          || pick(['[data-ph-at-id*="title"]', 'h1[class*="job"]', 'h1[class*="title"]', 'h1'])
          || '';
        const company = posting?.hiringOrganization?.name || '';
        const description = posting?.description ? htmlToText(posting.description) : '';
        const bodyText = description || jobPageBodyText().slice(0, 12000);
        const lines = [title || selfKey.split('/').filter(Boolean).pop()];
        if (company) lines.push('Company: ' + company);
        lines.push('URL: ' + location.href);
        if (bodyText) lines.push('', bodyText);
        jobs.push({ url: location.href, job_title: title, company, listing_text: lines.join('\n') });
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
    return jobPageBodyText(modal ? [modal] : []);
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

  // ai: claude-sonnet-5 | 2026-10-20
  // Dashboard homepage's "Saved" section only -- see detectPage's wttjSaved
  // branch for why this must stay scoped to [data-testid="shortlist-section"]
  // rather than querying preview-card page-wide. Each card's "View" link
  // (excluding the sibling "Apply" link, which also matches /dashboard/jobs/)
  // resolves to /dashboard/jobs/<id> -- an authenticated dashboard route, not
  // the public /jobs/<company-slug>/<id> URL (confirmed: the same job id
  // tPnUCSwy appears at both /dashboard/jobs/tPnUCSwy here and
  // /jobs/batch-end/tPnUCSwy publicly, but the company-slug segment isn't
  // derivable from the displayed company name). Captured as-is: the existing
  // import pipeline already falls back to a browser-extension recapture when
  // a WTTJ URL can't be fetched server-side (see project notes on the WTTJ
  // ATS resolution gap), which applies here exactly the same as it would for
  // any other WTTJ URL.
  function buildWttjSavedPayload() {
    const section = document.querySelector('[data-testid="shortlist-section"]');
    const cards = section ? section.querySelectorAll('[data-testid="preview-card"]') : [];
    const jobs = [];
    cards.forEach(card => {
      const company = (card.querySelector('[data-testid="job-company-name"]')?.innerText || '').trim();
      const title = (card.querySelector('[data-testid="job-title"]')?.innerText || '').trim();
      const viewLink = card.querySelector('a[href*="/dashboard/jobs/"]:not([data-testid="apply-button-link"])');
      let url = '';
      if (viewLink) {
        try { url = new URL(viewLink.getAttribute('href'), location.href).href; } catch { /* leave blank */ }
      }
      if (!url) return;
      const lines = [title];
      if (company) lines.push(`Company: ${company}`);
      lines.push(`URL: ${url}`);
      jobs.push({ url, job_title: title, company, listing_text: lines.join('\n') });
    });
    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ─── Oracle Fusion Recruiting Cloud (Candidate Experience) ─────────────────

  // ai: claude-sonnet-5 | 2026-08-28
  // The result count is only rendered as page text ("14 OPEN JOBS"), not a
  // data attribute. Falls back to counting unique job ids among the search
  // list's card links (still mounted in the DOM behind an open preview
  // modal) if that text isn't found.
  function oracleHcmTotalHits() {
    const m = document.body.innerText.match(/(\d+)\s+OPEN JOBS?/i);
    if (m) return parseInt(m[1], 10);
    const ids = new Set();
    document.querySelectorAll('a.job-list-item__link[href]').forEach(a => {
      try {
        const mm = new URL(a.href).pathname.match(/\/job\/(\d+)/i);
        if (mm) ids.add(mm[1]);
      } catch { /* skip */ }
    });
    return ids.size;
  }

  // ai: claude-sonnet-5 | 2026-08-28
  // Oracle embeds a clean top-level schema.org JobPosting block (unlike
  // RobertHalf's WebPage-wrapped mainEntity shape) — readJobPostingLd()
  // already handles it as-is. Used for both the standalone job page
  // (oracleHcmJob) and the "detail" capture mode of the split-view preview
  // modal (oracleHcmSplit), since the JSON-LD updates identically either way.
  function buildOracleHcmJobPayload() {
    const posting = readJobPostingLd(document);
    const title = posting?.title || pick(['h1[class*="title"]', 'h1']);
    const company = posting?.hiringOrganization?.name || '';
    const reqId = posting?.identifier?.value || '';
    const description = posting?.description
      ? htmlToText(posting.description)
      : jobPageBodyText().slice(0, 12000);
    const url = location.origin + location.pathname;

    return JSON.stringify({
      jobs: [{
        url,
        job_title: title,
        company,
        listing_text: jobPostingLines({ title, company, reqId, url, description }),
      }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-08-28
  // "List" capture mode for the split-view preview modal — harvests every
  // job card from the search-results list still mounted behind the modal.
  // Cards carry title + location + posted-date as plain text, no per-job
  // fetch needed. Every job on a given tenant's site is the same employer,
  // so the currently-open posting's JSON-LD company applies to all of them.
  // Best-effort, not exhaustive: the list is virtual-scrolled, so a card
  // outside the current scroll position + buffer is mounted with real size
  // but zero content until scrolled into view — those are skipped (empty
  // title) rather than captured. jobCount (from oracleHcmTotalHits) can
  // therefore be larger than what this actually returns.
  function buildOracleHcmSplitListPayload() {
    const company = readJobPostingLd(document)?.hiringOrganization?.name || '';
    const seen = new Set();
    const jobs = [];
    document.querySelectorAll('a.job-list-item__link[href]').forEach(link => {
      let u;
      try { u = new URL(link.href); } catch { return; }
      const m = u.pathname.match(/\/job\/(\d+)/i);
      if (!m || seen.has(m[1])) return;
      seen.add(m[1]);

      const cardText = (link.innerText || '').trim();
      const title = cardText.split('\n')[0]?.trim() || '';
      if (!title) return; // skip icon-only duplicate links with no visible text
      const url = u.origin + u.pathname;
      const lines = [title];
      if (company) lines.push('Company: ' + company);
      lines.push('URL: ' + url);
      if (cardText) lines.push('', cardText);
      jobs.push({ url, job_title: title, company, listing_text: lines.join('\n') });
    });
    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ─── Indeed ─────────────────────────────────────────────────────────────────

  // ai: claude-sonnet-5 | 2026-09-22
  // The standalone job page (/viewjob?jk=<key>) uses the exact same detail-pane
  // DOM shape as the split-view search page's right-hand pane, so
  // buildIndeedJobPayload() below is shared between indeedJob and the "detail"
  // capture mode of indeedSplit — same pattern as Oracle's buildOracleHcmJobPayload.
  // ai: claude-sonnet-5 | 2026-09-22
  // On first paint of the split view, Indeed auto-opens the first result in
  // the detail pane but doesn't add ?vjk= to the URL until the user actually
  // clicks a card — so the query-string lookup alone misses that initial
  // state. Fall back to matching the detail pane's title against the list's
  // title links (both are on-page simultaneously) to recover its key.
  function indeedJobKey() {
    const params = new URLSearchParams(location.search);
    const fromUrl = params.get('vjk') || params.get('jk');
    if (fromUrl) return fromUrl;
    const title = document.querySelector('[data-testid="vj-job-title"]')?.innerText.trim();
    if (!title) return '';
    const match = [...document.querySelectorAll('.job_seen_beacon a.jcs-JobTitle[data-jk]')]
      .find(a => a.innerText.trim() === title);
    return match?.getAttribute('data-jk') || '';
  }

  function indeedJobUrl(jk) {
    return jk ? `${location.origin}/viewjob?jk=${jk}` : location.origin + location.pathname;
  }

  // ai: claude-sonnet-5 | 2026-09-22
  // [data-testid="company-info-metadata"] packs company name and location as
  // newline-separated text with no per-field markup, and the standalone
  // /viewjob page repeats the location twice (once from the sticky header,
  // once from a duplicate row) where the split-view detail pane only shows it
  // once — dedupe lines rather than assume a fixed line count.
  function buildIndeedJobPayload() {
    const jk = indeedJobKey();
    const url = indeedJobUrl(jk);
    const title = pick(['[data-testid="vj-job-title"]', 'h1']);
    // The star-rating badge (e.g. "AmeriSave Mortgage Corp. · 2.6 ★ Remote")
    // renders as its own "·" separator line and bare numeric rating line
    // between company and location — drop both rather than let them leak
    // into the location field as noise.
    const metaLines = [...new Set(
      pick(['[data-testid="company-info-metadata"]']).split('\n')
        .map(l => l.trim())
        .filter(l => l && l !== '·' && !/^\d+(\.\d+)?$/.test(l))
    )];
    const company = metaLines[0] || '';
    const jobLocation = metaLines.slice(1).join(', ');
    const descEl = document.querySelector('.simple-job-description-html');
    const description = descEl ? descEl.innerText.trim() : jobPageBodyText().slice(0, 12000);

    const lines = [title];
    if (company) lines.push('Company: ' + company);
    if (jobLocation) lines.push('Location: ' + jobLocation);
    lines.push('URL: ' + url);
    if (description) lines.push('', description);

    return JSON.stringify({
      jobs: [{ url, job_title: title, company, listing_text: lines.join('\n') }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-09-22
  // "List" capture mode for the split view — harvests every job card visible
  // in the left-hand results list rather than just the one open on the right.
  // Cards carry dedicated testids for title/company/location (company-name,
  // text-location) plus a data-jk job key on the title link, so no per-job
  // fetch is needed.
  function buildIndeedSplitListPayload() {
    const seen = new Set();
    const jobs = [];
    document.querySelectorAll('.job_seen_beacon').forEach(card => {
      const link = card.querySelector('a.jcs-JobTitle[data-jk]');
      const jk = link?.getAttribute('data-jk');
      if (!jk || seen.has(jk)) return;
      seen.add(jk);

      const title = link.innerText.trim();
      if (!title) return; // skip icon-only duplicate links with no visible text
      const company = card.querySelector('[data-testid="company-name"]')?.innerText.trim() || '';
      const jobLocation = card.querySelector('[data-testid="text-location"]')?.innerText.trim() || '';
      const url = indeedJobUrl(jk);

      const lines = [title];
      if (company) lines.push('Company: ' + company);
      if (jobLocation) lines.push('Location: ' + jobLocation);
      lines.push('URL: ' + url);
      const cardText = card.innerText.trim();
      if (cardText) lines.push('', cardText);

      jobs.push({ url, job_title: title, company, listing_text: lines.join('\n') });
    });
    return JSON.stringify({ jobs, source: { url: location.href } });
  }

  // ─── Jobgether ─────────────────────────────────────────────────────────────

  // ai: gpt-5 | 2026-10-04
  function collectJobgetherMatchLinks() {
    const matches = new Map();
    document.querySelectorAll('a[href*="/offer/"]').forEach(link => {
      let offerUrl;
      try { offerUrl = new URL(link.href, location.href); } catch { return; }
      if (!isHostOrSubdomain(offerUrl.hostname, 'jobgether.com') || !/^\/offer\//.test(offerUrl.pathname)) return;
      const canonicalUrl = offerUrl.origin + offerUrl.pathname;
      if (!matches.has(canonicalUrl)) matches.set(canonicalUrl, link);
    });
    return [...matches.entries()].map(([url, link]) => ({ url, link }));
  }

  // ai: gpt-5 | 2026-10-04
  function buildJobgetherMatchesPayload() {
    const jobs = collectJobgetherMatchLinks().map(({ url, link }) => {
      let card = link.closest('article, li, [role="listitem"]');
      if (!card) {
        card = link.parentElement;
        for (let depth = 0; depth < 6 && card && card !== document.body; depth++) {
          const textLength = (card.innerText || '').trim().length;
          if (textLength >= 30 && textLength <= 2000) break;
          card = card.parentElement;
        }
      }

      const cardText = (card?.innerText || link.innerText || '').trim().slice(0, 2000);
      const heading = card?.querySelector('h1, h2, h3, h4, [role="heading"]');
      const title = (heading?.innerText || link.innerText || '').split('\n')[0].trim();
      const scoreMatch = cardText.match(/\b(\d{1,3})\s*%\s*(?:match)?\b/i);
      const lines = [title || url.split('/').pop(), `URL: ${url}`];
      if (scoreMatch) lines.push(`Your match score: ${scoreMatch[1]}%`);
      if (cardText) lines.push('', cardText);
      return { url, job_title: title, company: '', listing_text: lines.join('\n') };
    });

    if (jobs.length === 0) return buildHtmlPayload();
    return JSON.stringify({
      jobs,
      source: { url: location.href, name: 'Jobgether Matches', platform: 'jobgether' },
    });
  }

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
  // ai: claude-sonnet-5 | 2026-10-06
  // Job 5501 (Staff Software Engineer, FoodStorm) shipped with company
  // "FoodStorm" instead of "Instacart" -- the server-side guess from raw page
  // text picked the brand name adjacent to the title instead of the real
  // employer, which only happens when BOTH extraction paths above came back
  // empty. The old wait loop used the match-score widget's appearance as a
  // proxy for "the page has hydrated," but that widget can render before the
  // company link/title-suffix data (a separate, slower API response) lands.
  // Wait for company readiness directly instead of an unrelated proxy signal.
  async function buildJobgetherJobPayload() {
    const companyReady = () =>
      !!extractJobgetherCompanyFromLink() || /\bat\s+.+/i.test(document.title);
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      if (companyReady() && document.querySelector('[data-match-score]')) break;
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
    const bodyText = jobPageBodyText().slice(0, 12000);

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

  // ai: claude-sonnet-5 | 2026-10-06
  // Sanity check for split-view "list" mode captures (LinkedIn/Indeed/Insight
  // Global/Oracle): a DOM redesign can silently defeat the card-level
  // extraction selectors while a lone leftover element (e.g. the open detail
  // pane's own link) still yields a non-zero, plausible-looking result -- see
  // the 2026-10-06 LinkedIn break in project memory "split-view-capture-mode",
  // where "All results" mode captured only the single expanded job instead of
  // ~25, with no error anywhere in the chain. Comparing the extracted count
  // against an independent, non-card-based expected count (where the site
  // renders one) catches that failure mode. The >=3/<=1 thresholds are
  // deliberately loose so this doesn't flag Oracle's legitimate partial
  // captures from virtual scrolling (buildOracleHcmSplitListPayload can
  // genuinely return fewer than the total "OPEN JOBS" count) -- only a
  // near-total collapse (0 or 1 captured when several were expected) warns.
  function listCaptureWarning(jobsJson, expectedCount) {
    if (expectedCount == null || expectedCount <= 0) return null;
    let n;
    try { n = JSON.parse(jobsJson).jobs.length; } catch { return null; }
    if (n === 0) {
      return `Captured 0 jobs, but the page reports ${expectedCount}. The site's layout may have changed — check it manually before relying on this capture.`;
    }
    if (n <= 1 && expectedCount >= 3) {
      return `Captured only ${n} job, but the page reports ${expectedCount}. The site's layout may have changed and this capture is likely missing most of the list.`;
    }
    return null;
  }

  async function buildPayload(options = {}) {
    const page = await detectPage();
    let payload;
    let warning = null;
    switch (page.type) {
      case 'blocked':          payload = null; break;
      case 'linkedInJob':      payload = await buildLinkedInJobPayload(); break;
      case 'linkedInSearch':
        if (options.captureMode === 'detail') {
          payload = await buildLinkedInSearchDetailPayload();
        } else {
          payload = buildLinkedInSearchPayload();
          warning = listCaptureWarning(payload, linkedInStatedResultCount());
        }
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
      case 'ashbySearch':      payload = await buildAshbySearchPayload(); break;
      case 'wttjJob':          payload = await buildWttjJobPayload(); break;
      case 'wttjSaved':        payload = buildWttjSavedPayload(); break;
      case 'robertHalfJob':    payload = buildRobertHalfJobPayload(); break;
      case 'jibeJob':          payload = buildJibeJobPayload(); break;
      case 'jibeSearch':       payload = await buildJibeSearchPayload(); break;
      case 'icimsJob':         payload = buildIcimsJobPayload(); break;
      case 'icimsSearch':      payload = await buildIcimsSearchPayload(); break;
      case 'phenomSearch':
        payload = await buildPhenomSearchPayload();
        warning = phenomFallbackWarning(payload); // ai: claude-opus-5-5 | 2026-10-29 -- its fallbacks can come up short too
        break;
      case 'insightGlobalSearch': payload = await buildInsightGlobalSearchPayload(); break;
      case 'insightGlobalSplit':
        if (options.captureMode === 'detail') {
          payload = buildInsightGlobalSplitDetailPayload();
        } else {
          payload = await buildInsightGlobalSplitListPayload(options);
          warning = listCaptureWarning(payload, insightGlobalTotalHits());
        }
        break;
      case 'kforceJob':        payload = buildKforceJobPayload(); break;
      case 'kforceSearch':     payload = await buildKforceSearchPayload(options); break;
      case 'jobanniSplit':
        if (options.captureMode === 'detail') {
          payload = buildJobanniDetailPayload();
        } else {
          payload = await buildJobanniListPayload();
          warning = listCaptureWarning(payload, jobanniTotalHits());
        }
        break;
      case 'jobgetherJob':     payload = await buildJobgetherJobPayload(); break;
      case 'jobgetherMatches': payload = buildJobgetherMatchesPayload(); break;
      case 'oracleHcmJob':     payload = buildOracleHcmJobPayload(); break;
      case 'oracleHcmSplit':
        if (options.captureMode === 'list') {
          payload = buildOracleHcmSplitListPayload();
          warning = listCaptureWarning(payload, oracleHcmTotalHits());
        } else {
          payload = buildOracleHcmJobPayload();
        }
        break;
      case 'indeedJob':        payload = buildIndeedJobPayload(); break;
      case 'indeedSplit':
        if (options.captureMode === 'list') {
          payload = buildIndeedSplitListPayload();
          // ai: claude-sonnet-5 | 2026-10-06
          // Indeed's results page has no independent "X jobs" count text
          // (confirmed live, 2026-10-06) -- this reuses the same
          // .job_seen_beacon selector detectPage()'s cardCount already uses,
          // so unlike the other three split-view sites above, it can't catch
          // a full selector rename (both counts would collapse together, as
          // happened with LinkedIn pre-fix). It only catches an extraction
          // bug within an otherwise-intact card set.
          warning = listCaptureWarning(payload, document.querySelectorAll('.job_seen_beacon').length);
        } else {
          payload = buildIndeedJobPayload();
        }
        break;
      case 'elasticSearch':
        payload = await buildElasticSearchPayload();
        warning = listCaptureWarning(payload, page.jobCount);
        break;
      case 'smartGeneric':
        payload = await buildSmartGenericPayload();
        if (isPhenomSearchPage()) warning = phenomFallbackWarning(payload); // ai: claude-opus-5-5 | 2026-10-29
        break;
      // ai: claude-sonnet-4-6 | 2026-06-26
      // For pages that don't match a known platform, extract body text rather
      // than sending raw HTML. The HTML import path scans for job links, which
      // yields nothing for individual job pages. Body text gives the importer
      // enough content to extract the description.
      default:                 payload = await buildGenericJobPagePayload(); break;
    }
    return { payload: withExtensionVersion(payload), warning };
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

  // ai: claude-sonnet-5 | 2026-08-28 — prefer JSON-LD JobPosting (see
  // readJobPostingLd) over DOM heading scraping for the same reason as the
  // buildSmartGenericPayload self-inclusion block above.
  async function buildGenericJobPagePayload() {
    await expandCollapsedSections();
    const posting = readJobPostingLd(document);
    const title = posting?.title || pick([
      'h1[class*="job"]', 'h1[class*="title"]',
      '[data-testid*="title"]', '[data-testid*="job-title"]',
      'h1',
    ]);
    const company = posting?.hiringOrganization?.name || '';
    const description = posting?.description ? htmlToText(posting.description) : '';
    const bodyText = description || jobPageBodyText().slice(0, 12000);
    const lines = [];
    if (title) lines.push(title);
    if (company) lines.push('Company: ' + company);
    lines.push('URL: ' + location.href);
    if (bodyText) lines.push('', bodyText);
    return JSON.stringify({
      jobs: [{ url: location.href, job_title: title, company, listing_text: lines.join('\n') }],
      source: { url: location.href },
    });
  }

  // ai: claude-sonnet-5 | 2026-10-12
  // Exposed so popup.js can probe every frame for the real job-board frame in
  // one chrome.scripting.executeScript({allFrames:true, func}) round trip
  // (same pattern autofill.js already uses via window.__jhaDetectAutofillFrame)
  // instead of only ever reaching frame 0 via chrome.tabs.sendMessage. Needed
  // for ATS widgets a company embeds as a cross-origin iframe on its own
  // careers page (confirmed live: clickup.com/careers embeds
  // jobs.ashbyhq.com/clickup this way) -- frame 0's own detectPage() can't see
  // into that iframe at all and falls back to a whole-page generic capture.
  window.__jhaDetectPage = detectPage;

  // ─── Message Listener ──────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.action === 'build') {
      buildPayload(message.options || {})
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ error: String(err) }));
      return true; // keep channel open for async response
    }

    // Context menu flow: background can't wait for a long async response,
    // so content script sends the result back proactively via runtime message.
    if (message.action === 'buildForBackground') {
      detectPage().then(page => {
        if (page.type === 'blocked') {
          chrome.runtime.sendMessage({ action: 'payloadReady', payload: null, blocked: true, site: page.site });
          return;
        }
        buildPayload()
          .then(({ payload, warning }) => chrome.runtime.sendMessage({ action: 'payloadReady', payload, warning }))
          .catch(() => chrome.runtime.sendMessage({ action: 'payloadReady', payload: null }));
      });
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
