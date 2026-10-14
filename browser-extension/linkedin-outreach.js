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

// ai: claude-opus-5-5 | 2026-10-14
// LinkedIn outreach helper, injected by background.js (see its
// "LinkedIn outreach" section) into a contact's profile page and then into the
// messaging page LinkedIn's Message link leads to. It only ever finds the
// Message link and fills the compose box -- it never clicks Send; the user
// reviews the message and sends it themselves.
//
// LinkedIn's class names are obfuscated and change between deploys, so every
// lookup here keys off URLs, ARIA roles, and contenteditable instead.
(() => {
  if (window.__jhaLinkedInOutreachLoaded) return;
  window.__jhaLinkedInOutreachLoaded = true;

  const WAIT_TIMEOUT_MS = 15000;
  const POLL_INTERVAL_MS = 250;
  const PASTE_SETTLE_MS = 400;

  function isVisible(el) {
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  async function waitFor(find) {
    const deadline = Date.now() + WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const found = find();
      if (found) return found;
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
    return null;
  }

  // The profile's own Message link (top card), not one from a "People also
  // viewed" sidebar: prefer a visible link inside <main>. On 2026's LinkedIn
  // this is an <a href="/messaging/compose/?profileUrn=...&recipient=...">
  // that navigates the whole tab to /messaging/thread/new/.
  function findComposeLink() {
    const links = [...document.querySelectorAll('a[href*="/messaging/compose/"]')].filter(isVisible);
    return links.find((link) => link.closest('main')) || null;
  }

  function findComposeBox() {
    const candidates = [
      ...document.querySelectorAll('main [contenteditable="true"][role="textbox"]'),
      ...document.querySelectorAll('.msg-form__contenteditable[contenteditable="true"]'),
      ...document.querySelectorAll('main form [contenteditable="true"]'),
    ];
    return candidates.find(isVisible) || null;
  }

  function boxText(box) {
    return (box.innerText || box.textContent || '').replace(/\s+/g, ' ').trim();
  }

  // A synthetic paste is what a person pasting would produce, and rich-text
  // editors handle multi-line plain text from it well; execCommand insertText
  // is the fallback for an editor that ignores untrusted paste events. The
  // settle wait keeps an editor that applies the paste asynchronously from
  // looking like it ignored it -- which would insert the message twice.
  async function insertMessage(box, message) {
    box.focus();
    const probe = message.replace(/\s+/g, ' ').trim().slice(0, 40);
    try {
      const data = new DataTransfer();
      data.setData('text/plain', message);
      box.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    } catch {
      // ClipboardEvent construction unsupported; fall through to execCommand.
    }
    await new Promise((resolve) => setTimeout(resolve, PASTE_SETTLE_MS));
    if (boxText(box).includes(probe)) return true;
    document.execCommand('insertText', false, message);
    return boxText(box).includes(probe);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.action === 'findLinkedInComposeUrl') {
      waitFor(findComposeLink).then((link) =>
        sendResponse(link ? { ok: true, url: link.href } : { ok: false, error: 'no Message button on this profile' })
      );
      return true; // keep channel open for the async response
    }
    if (message.action === 'insertLinkedInOutreachDraft') {
      waitFor(findComposeBox).then(async (box) => {
        if (!box) {
          sendResponse({ ok: false, error: 'message box not found' });
          return;
        }
        if (boxText(box)) {
          // Never clobber something already typed (e.g. an earlier draft).
          sendResponse({ ok: false, error: 'the message box already has text in it' });
          return;
        }
        const inserted = await insertMessage(box, message.message);
        sendResponse(inserted ? { ok: true } : { ok: false, error: 'paste was not accepted' });
      });
      return true;
    }
    return false;
  });
})();
