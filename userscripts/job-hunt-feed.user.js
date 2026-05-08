// ==UserScript==
// @name         Manual HTML to Job Hunt App
// @description  install a button on a matched page that will feed the html to my job scanner
// @namespace    http://tampermonkey.net/
// @version      2026-05-07
// @match        https://wellfound.com/jobs/*
// @match        https://wellfound.com/jobs
// @match        https://app.welcometothejungle.com/jobs/*
// @match        https://wellfound.com/jobs?*
// @match        https://my.greenhouse.io/jobs*
// @match        https://job-boards.greenhouse.io/*/jobs/*
// @match        https://www.linkedin.com/jobs/view/*
// @match        https://www.linkedin.com/jobs/collections/recommended/*
// @match        https://www.linkedin.com/jobs/search-results*
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
    btn.style.cssText = 'position:fixed;top:10px;right:10px;z-index:9999;padding:10px 14px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;';

    let reattachTimer = null;
    function attachBtn() {
        clearTimeout(reattachTimer);
        // Debounce: wait for React to finish its current batch of DOM mutations
        // before re-injecting, otherwise we race against hydration passes.
        reattachTimer = setTimeout(() => {
            if (!document.getElementById('__jha_feed_btn')) {
                document.body.appendChild(btn);
            }
        }, 600);
    }
    attachBtn();

    // Re-attach if a React re-render removes the button from the DOM.
    new MutationObserver(attachBtn).observe(document.body, { childList: true, subtree: false });

    // Returns the first non-empty innerText from a list of CSS selectors.
    function pick(selectors) {
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.innerText.trim()) return el.innerText.trim();
        }
        return '';
    }

    function buildLinkedInJobPayload() {
        // Try to expand the truncated description before grabbing text.
        const seeMoreBtn = document.querySelector(
            'button.show-more-less-html__button--more, ' +
            'button[aria-label*="more description"], ' +
            'button[aria-label*="Click to see more"]'
        );
        if (seeMoreBtn) seeMoreBtn.click();

        const title = pick([
            'h1.top-card-layout__title',
            '.job-details-jobs-unified-top-card__job-title h1',
            '.jobs-unified-top-card__job-title h1',
            'h1',
        ]);
        const company = pick([
            '.top-card-layout__second-subline a',
            '.job-details-jobs-unified-top-card__company-name a',
            '.topcard__org-name-link',
            '.jobs-unified-top-card__company-name a',
        ]);
        const description = pick([
            '.show-more-less-html__markup',
            '.jobs-description-content__text',
            '.jobs-box__html-content',
            '#job-details',
        ]);

        const lines = [];
        if (title)       lines.push(title);
        if (company)     lines.push('Company: ' + company);
        lines.push('URL: ' + window.location.href);
        if (description) lines.push('', description);

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

    function buildHtmlPayload() {
        return JSON.stringify({ content: document.documentElement.outerHTML, url: window.location.href });
    }

    btn.onclick = () => {
        const isJobView = /\/jobs\/view\/\d+/.test(window.location.pathname);
        const data = isJobView ? buildLinkedInJobPayload() : buildHtmlPayload();

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
