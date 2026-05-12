// ==UserScript==
// @name         Manual HTML to Job Hunt App
// @description  install a button on a matched page that will feed the html to my job scanner
// @namespace    http://tampermonkey.net/
// @version      2026-05-10
// @match        https://wellfound.com/jobs/*
// @match        https://wellfound.com/jobs
// @match        https://app.welcometothejungle.com/jobs/*
// @match        https://wellfound.com/jobs?*
// @match        https://careers.teradata.com/jobs*
// @match        https://my.greenhouse.io/jobs*
// @match        https://job-boards.greenhouse.io/*/jobs/*
// @match        https://www.linkedin.com/jobs/view/*
// @match        https://www.linkedin.com/jobs/collections/recommended/*
// @match        https://www.linkedin.com/jobs/search/*
// @match        https://www.linkedin.com/jobs/search-results*
// @match        https://www.randstadusa.com/jobs/q-software-engineer/remote/
// @icon         data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    const btn = document.createElement('button');
    btn.id = '__jha_feed_btn';
    btn.textContent = 'Feed to App';
    btn.style.cssText = 'position:fixed;top:10px;right:10px;z-index:9999;padding:10px 14px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:grab;touch-action:none;';

    let reattachTimer = null;
    let suppressNextClick = false;
    const buttonPositionKey = '__jha_feed_btn_position';

    function clampButtonPosition(left, top) {
        const rect = btn.getBoundingClientRect();
        const maxLeft = Math.max(0, window.innerWidth - rect.width);
        const maxTop = Math.max(0, window.innerHeight - rect.height);
        return {
            left: Math.min(Math.max(0, left), maxLeft),
            top: Math.min(Math.max(0, top), maxTop),
        };
    }

    function setButtonPosition(left, top) {
        const position = clampButtonPosition(left, top);
        btn.style.left = `${position.left}px`;
        btn.style.top = `${position.top}px`;
        btn.style.right = 'auto';
        btn.style.bottom = 'auto';
        return position;
    }

    function saveButtonPosition() {
        const rect = btn.getBoundingClientRect();
        const position = setButtonPosition(rect.left, rect.top);
        try {
            localStorage.setItem(buttonPositionKey, JSON.stringify(position));
        } catch (storageError) {
            console.warn('Could not save Job Hunt button position:', storageError);
        }
    }

    function restoreButtonPosition() {
        let savedPosition;
        try {
            savedPosition = JSON.parse(localStorage.getItem(buttonPositionKey) || 'null');
        } catch (storageError) {
            console.warn('Could not restore Job Hunt button position:', storageError);
            return;
        }

        if (
            !savedPosition ||
            !Number.isFinite(savedPosition.left) ||
            !Number.isFinite(savedPosition.top)
        ) {
            return;
        }

        setButtonPosition(savedPosition.left, savedPosition.top);
    }

    btn.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;

        const startX = event.clientX;
        const startY = event.clientY;
        const startRect = btn.getBoundingClientRect();
        let didDrag = false;

        btn.setPointerCapture(event.pointerId);
        btn.style.cursor = 'grabbing';

        function handlePointerMove(moveEvent) {
            const deltaX = moveEvent.clientX - startX;
            const deltaY = moveEvent.clientY - startY;
            if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) {
                didDrag = true;
            }
            setButtonPosition(startRect.left + deltaX, startRect.top + deltaY);
        }

        function handlePointerUp() {
            btn.releasePointerCapture(event.pointerId);
            btn.style.cursor = 'grab';
            btn.removeEventListener('pointermove', handlePointerMove);
            btn.removeEventListener('pointerup', handlePointerUp);
            btn.removeEventListener('pointercancel', handlePointerUp);

            if (didDrag) {
                suppressNextClick = true;
                saveButtonPosition();
            }
        }

        btn.addEventListener('pointermove', handlePointerMove);
        btn.addEventListener('pointerup', handlePointerUp);
        btn.addEventListener('pointercancel', handlePointerUp);
    });

    function attachBtn() {
        clearTimeout(reattachTimer);
        // Debounce: wait for React to finish its current batch of DOM mutations
        // before re-injecting, otherwise we race against hydration passes.
        reattachTimer = setTimeout(() => {
            if (!document.getElementById('__jha_feed_btn')) {
                document.body.appendChild(btn);
                restoreButtonPosition();
            }
            updateBtnText();
        }, 600);
    }
    attachBtn();

    function updateBtnText() {
        const isSearch = /\/jobs\/search/.test(window.location.pathname) ||
                        /\/jobs\/collections/.test(window.location.pathname) ||
                        /\/jobs\/search-results/.test(window.location.pathname);
        if (isSearch && window.location.hostname.includes('linkedin.com')) {
            const jobIds = new Set();

            // 1. Aggressive Attribute Crawl: find anything that looks like a Job ID
            // LinkedIn uses data-job-id, data-occludable-job-id, and others for virtualized lists.
            const allElements = document.querySelectorAll('[data-job-id], [data-occludable-job-id], [data-view-name="job-card"], .job-card-container, .job-card-list__container');
            allElements.forEach(el => {
                // Check common attributes
                const id = el.getAttribute('data-job-id') || el.getAttribute('data-occludable-job-id');
                if (id && /^\d{8,}/.test(id)) jobIds.add(id);

                // Check links inside this element
                el.querySelectorAll('a[href]').forEach(a => {
                    const viewMatch = a.href.match(/\/view\/(\d+)/);
                    if (viewMatch) jobIds.add(viewMatch[1]);
                    const queryMatch = a.href.match(/[?&]currentJobId=(\d+)/);
                    if (queryMatch) jobIds.add(queryMatch[1]);
                });
            });

            // 2. Global Link Scan as fallback
            if (jobIds.size <= 1) {
                document.querySelectorAll('a[href*="/view/"], a[href*="currentJobId="]').forEach(a => {
                    const m = a.href.match(/\/view\/(\d+)/) || a.href.match(/[?&]currentJobId=(\d+)/);
                    if (m) jobIds.add(m[1]);
                });
            }

            btn.textContent = `Feed ${jobIds.size} jobs to App`;
        } else {
            btn.textContent = 'Feed to App';
        }
    }

    // Re-attach if a React re-render removes the button from the DOM.
    // Use subtree: true to catch the job list loading inside the layout.
    new MutationObserver(attachBtn).observe(document.body, { childList: true, subtree: true });

    // Also update text on scroll as more jobs might load
    window.addEventListener('scroll', () => {
        if (window.location.hostname.includes('linkedin.com')) {
            updateBtnText();
        }
    }, { passive: true });

    window.addEventListener('resize', () => {
        if (document.body.contains(btn)) {
            saveButtonPosition();
        }
    });

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // Returns the first non-empty innerText from a list of CSS selectors.
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

    function cleanLinkedInTitle(value) {
        return (value || '')
            .replace(/\s*\|\s*LinkedIn\s*$/i, '')
            .replace(/\s+at\s+.+$/i, '')
            .trim();
    }

    function cleanLinkedInCompany(value) {
        return (value || '')
            .replace(/\s*\|\s*LinkedIn\s*$/i, '')
            .trim();
    }

    function titleAndCompanyFromDocumentTitle() {
        const cleanTitle = (document.title || '').replace(/\s*\|\s*LinkedIn\s*$/i, '').trim();
        const match = cleanTitle.match(/^(.+?)\s+at\s+(.+)$/i);
        if (!match) return { title: '', company: '' };
        return {
            title: match[1].trim(),
            company: match[2].trim(),
        };
    }

    async function buildLinkedInJobPayload() {
        // Try to expand the truncated description before grabbing text.
        const seeMoreBtn = document.querySelector(
            'button.show-more-less-html__button--more, ' +
            'button[aria-label*="more description"], ' +
            'button[aria-label*="Click to see more"], ' +
            'button[aria-label*="See more"]'
        );
        if (seeMoreBtn) {
            seeMoreBtn.click();
            await sleep(350);
        }

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
        const titleFallback = titleAndCompanyFromDocumentTitle();
        const title = cleanLinkedInTitle(titleFromPage) || titleFallback.title;
        const company = cleanLinkedInCompany(companyFromPage) || titleFallback.company;
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
        if (title)       lines.push(title);
        if (company)     lines.push('Company: ' + company);
        lines.push('URL: ' + window.location.href);
        if (description) lines.push('', description);
        if (!title && !company && !description) {
            lines.push('', document.body.innerText.trim().slice(0, 12000));
        }

        // Send as { jobs: [...] } — the listener's JSON path handles this
        // and feeds listing_text into raw_email_snippet + the fit scorer.
        return JSON.stringify({
            jobs: [{
                url: window.location.href,
                job_title: title,
                company: company,
                listing_text: lines.join('\n'),
            }],
        });
    }

    function buildLinkedInSearchPayload() {
        const jobIds = new Set();
        const jobs = [];

        // Target specifically the left-side scrolling cards
        const cardElements = document.querySelectorAll(
            '[data-view-name="job-card"], ' +
            '[data-job-id], ' +
            '[data-occludable-job-id], ' +
            '.job-card-list__container, ' +
            '.job-card-container'
        );

        cardElements.forEach(card => {
            const id = card.getAttribute('data-job-id') ||
                       card.getAttribute('data-occludable-job-id') ||
                       (card.querySelector('a[href*="/view/"]') ? card.querySelector('a[href*="/view/"]').href.match(/\/view\/(\d+)/)[1] : null);

            if (!id || jobIds.has(id)) return;
            jobIds.add(id);

            const titleEl = card.querySelector(
                '.job-card-list__title, ' +
                '.job-card-container__link, ' +
                '.artdeco-entity-lockup__title, ' +
                'a' // fallback to first link text
            );

            const companyEl = card.querySelector(
                '.job-card-container__company-name, ' +
                '.artdeco-entity-lockup__subtitle, ' +
                '.job-card-container__primary-description, ' +
                '.job-card-container__company-link'
            );

            const url = `https://www.linkedin.com/jobs/view/${id}/`;
            const title = titleEl ? titleEl.innerText.trim() : 'Unknown Title';
            const company = companyEl ? companyEl.innerText.trim() : '';
            const metadata = card.innerText.trim();

            jobs.push({
                url: url,
                job_title: title,
                company: company,
                listing_text: `${title}\nCompany: ${company}\nURL: ${url}\n\nSnippet:\n${metadata}`,
            });
        });

        // Fallback for loose links not inside recognized cards
        if (jobs.length === 0) {
            document.querySelectorAll('a[href*="/view/"]').forEach(link => {
                const idMatch = link.href.match(/\/view\/(\d+)/);
                if (idMatch) {
                    const id = idMatch[1];
                    if (jobIds.has(id)) return;
                    jobIds.add(id);
                    const url = `https://www.linkedin.com/jobs/view/${id}/`;
                    jobs.push({
                        url: url,
                        job_title: link.innerText.trim() || 'LinkedIn Job',
                        company: '',
                        listing_text: `URL: ${url}`,
                    });
                }
            });
        }

        return JSON.stringify({ jobs });
    }

    function buildHtmlPayload() {
        return JSON.stringify({ content: document.documentElement.outerHTML, url: window.location.href });
    }

    btn.onclick = async () => {
        if (suppressNextClick) {
            suppressNextClick = false;
            return;
        }

        const isJobView = /\/jobs\/view\/\d+/.test(window.location.pathname);
        const isSearch = /\/jobs\/search/.test(window.location.pathname) ||
                        /\/jobs\/collections/.test(window.location.pathname) ||
                        /\/jobs\/search-results/.test(window.location.pathname);

        let data;
        if (isJobView) {
            data = await buildLinkedInJobPayload();
        } else if (isSearch && window.location.hostname.includes('linkedin.com')) {
            data = buildLinkedInSearchPayload();
        } else {
            data = buildHtmlPayload();
        }

        GM_xmlhttpRequest({
            method: 'POST',
            url: 'http://localhost:8765/import',
            data,
            headers: { 'Content-Type': 'application/json' },
            onload: (res) => {
                if (res.status >= 400) {
                    const body = JSON.parse(res.responseText || '{}');
                    alert('Error ' + res.status + ': ' + (body.error || res.responseText));
                } else {
                    alert('Sent! Check the app for import progress.');
                }
            },
            onerror: (err) => console.error('Failed to send:', err),
        });
    };
})();
