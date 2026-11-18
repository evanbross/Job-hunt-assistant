/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// Runs in LinkedIn's own JS world (manifest "world": "MAIN") from
// document_start, and only watches: it keeps the messages LinkedIn's messaging
// page itself fetches (GET /voyager/api/voyagerMessagingGraphQL/graphql with a
// messengerMessages.* queryId -- checked live 2026-11-18) so the popup's
// "Capture this conversation" can send the open thread to the app. No request
// of its own is ever made; every call passes straight through to the real fetch.
// background.js reads a thread back with window.__jhaLinkedInThread(id) (a
// world:'MAIN' executeScript), only when the user clicks Capture.
//
// From each com.linkedin.messenger.Message: deliveredAt (epoch ms), body.text,
// subject, sender.hostIdentityUrn (urn:li:fsd_profile:<member id>) and the
// sender's name and linkedin.com/in/<member id> link; the conversation's
// entityUrn, urn:li:msg_conversation:(urn:li:fsd_profile:<owner>,<thread id>),
// names the thread (the /messaging/thread/<id>/ in the URL) and its owner -- the
// signed-in user, so a message is theirs when its sender is the owner.
(() => {
  if (window.__jhaLinkedInThread) return;

  const GRAPHQL_PATH = '/voyager/api/voyagerMessagingGraphQL/graphql';
  const MESSAGES_QUERY_PREFIX = 'messengerMessages.';
  const MESSAGE_TYPE = 'com.linkedin.messenger.Message';
  const CONVERSATION_URN_RE = /^urn:li:msg_conversation:\(urn:li:fsd_profile:([^,]+),(.+)\)$/;
  // Threads kept, least recently seen dropped first: plenty for one capture,
  // bounded for a long browsing session.
  const MAX_CONVERSATIONS = 20;
  const MAX_DEPTH = 12;

  // thread id -> { owner, messages: Map(message urn -> message) }
  const conversations = new Map();

  function isMessagesRequest(url) {
    try {
      const parsed = new URL(url, location.href);
      return parsed.pathname === GRAPHQL_PATH
        && (parsed.searchParams.get('queryId') || '').startsWith(MESSAGES_QUERY_PREFIX);
    } catch {
      return false;
    }
  }

  function conversationFor(conversationId, owner) {
    const convo = conversations.get(conversationId) || { owner, messages: new Map() };
    conversations.delete(conversationId);
    conversations.set(conversationId, convo);
    while (conversations.size > MAX_CONVERSATIONS) conversations.delete(conversations.keys().next().value);
    return convo;
  }

  function fullName(member) {
    return [member?.firstName?.text, member?.lastName?.text].filter(Boolean).join(' ').trim();
  }

  function remember(message) {
    const match = CONVERSATION_URN_RE.exec(message.conversation?.entityUrn || '');
    const senderId = String(message.sender?.hostIdentityUrn || '').split(':').pop();
    if (!match || !senderId || typeof message.deliveredAt !== 'number') return;
    const [, owner, conversationId] = match;
    const member = message.sender?.participantType?.member;
    conversationFor(conversationId, owner).messages.set(
      message.entityUrn || message.backendUrn || `${message.deliveredAt}:${senderId}`,
      {
        sentAtMs: message.deliveredAt,
        senderId,
        fromOwner: senderId === owner,
        senderName: fullName(member),
        senderProfileUrl: member?.profileUrl || '',
        subject: message.subject || '',
        text: message.body?.text || '',
      }
    );
  }

  function forget(urns) {
    for (const convo of conversations.values()) {
      for (const urn of urns) convo.messages.delete(urn);
    }
  }

  function walk(value, depth) {
    if (!value || typeof value !== 'object' || depth > MAX_DEPTH) return;
    if (Array.isArray(value)) {
      for (const item of value) walk(item, depth + 1);
      return;
    }
    if (value._type === MESSAGE_TYPE || value.$type === MESSAGE_TYPE) {
      remember(value);
      return;
    }
    // A sync response's metadata lists messages deleted since the last sync.
    if (Array.isArray(value.deletedUrns)) forget(value.deletedUrns);
    for (const key of Object.keys(value)) walk(value[key], depth + 1);
  }

  // What a capture sends (backend/scanning/import_listener.py's
  // CaptureConversationRequest), or { error } when this thread can't be sent.
  window.__jhaLinkedInThread = (conversationId) => {
    const convo = conversations.get(conversationId);
    if (!convo || convo.messages.size === 0) return { error: 'not_seen' };
    const messages = [...convo.messages.values()].sort((a, b) => a.sentAtMs - b.sentAtMs);
    const others = new Map(); // sender id -> their latest message
    for (const message of messages) if (!message.fromOwner) others.set(message.senderId, message);
    if (others.size === 0) return { error: 'no_reply' };
    if (others.size > 1) return { error: 'group' };
    const [them] = others.values();
    return {
      conversation_id: conversationId,
      counterpart_name: them.senderName,
      counterpart_profile_url: them.senderProfileUrl,
      messages: messages.map((m) => ({
        sent_at_ms: m.sentAtMs,
        from_owner: m.fromOwner,
        sender_name: m.senderName,
        subject: m.subject,
        text: m.text,
      })),
    };
  };

  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    const response = realFetch.apply(this, arguments);
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input?.url;
    if (url && isMessagesRequest(url)) {
      // Attached before the page sees the promise, so the clone is taken
      // before the page reads the body.
      response
        .then((res) => (res.ok ? res.clone().json() : null))
        .then((json) => walk(json, 0))
        .catch(() => {});
    }
    return response;
  };
})();
