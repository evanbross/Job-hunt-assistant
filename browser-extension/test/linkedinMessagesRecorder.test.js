/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-11-18
// linkedinMessagesRecorder.js runs in LinkedIn's own JS world: it must keep the
// messages LinkedIn's messaging page fetches, hand one thread back in the shape
// the listener's capture route takes, and pass every request through untouched.
// The response shape is the one LinkedIn's messengerMessages query returned
// live on 2026-11-18; every id, name and message here is invented.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const SOURCE = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "linkedinMessagesRecorder.js"),
  "utf8",
);

const OWNER = { id: "ACoAAOwner", first: "Pat", last: "Seeker" };
const RITA = { id: "ACoAARita", first: "Rita", last: "Recruiter" };
const GIL = { id: "ACoAAGil", first: "Gil", last: "Group" };
const THREAD = "2-UmVjcnVpdGVyVGhyZWFk+/==";
const MESSAGES_URL =
  "https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql?queryId=messengerMessages.b88ffdf2" +
  `&variables=(conversationUrn:${encodeURIComponent(`urn:li:msg_conversation:(urn:li:fsd_profile:${OWNER.id},${THREAD})`)})`;

function message({ id, at, from, text, subject = "Staff role", thread = THREAD }) {
  return {
    _type: "com.linkedin.messenger.Message",
    entityUrn: `urn:li:msg_message:(urn:li:fsd_profile:${OWNER.id},2-${id})`,
    backendUrn: `urn:li:messagingMessage:2-${id}`,
    deliveredAt: at,
    subject,
    body: { _type: "com.linkedin.pemberly.text.AttributedText", attributes: [], text },
    sender: {
      _type: "com.linkedin.messenger.MessagingParticipant",
      hostIdentityUrn: `urn:li:fsd_profile:${from.id}`,
      participantType: {
        member: {
          profileUrl: `https://www.linkedin.com/in/${from.id}`,
          firstName: { text: from.first },
          lastName: { text: from.last },
        },
      },
    },
    conversation: {
      _type: "com.linkedin.messenger.Conversation",
      entityUrn: `urn:li:msg_conversation:(urn:li:fsd_profile:${OWNER.id},${thread})`,
    },
  };
}

function syncResponse(elements, deletedUrns = []) {
  return {
    data: {
      messengerMessagesBySyncToken: {
        _type: "com.linkedin.restli.common.CollectionResponse",
        metadata: { _type: "com.linkedin.messenger.SyncMetadata", deletedUrns, newSyncToken: "sync-1" },
        elements,
      },
    },
  };
}

// A response body that isn't JSON (an error page, say).
const NOT_JSON = Symbol("not json");

function loadRecorder() {
  const { window } = new JSDOM("<html><body></body></html>", {
    url: `https://www.linkedin.com/messaging/thread/${THREAD}/`,
    runScripts: "outside-only",
  });
  const bodies = new Map();
  const pageFetch = vi.fn(async (url) => {
    const body = bodies.get(String(url)) ?? {};
    const json = async () => {
      if (body === NOT_JSON) throw new SyntaxError("Unexpected token <");
      return body;
    };
    return { ok: true, json, clone: () => ({ json }) };
  });
  window.fetch = pageFetch;
  window.eval(SOURCE);
  const respond = async (url, body) => {
    bodies.set(url, body);
    const res = await window.fetch(url, { credentials: "include" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    return res;
  };
  return { window, pageFetch, respond };
}

const PITCH = message({ id: "m1", at: 1_762_298_888_637, from: RITA, text: "Hi Pat, a Staff role?" });
const REPLY = message({ id: "m2", at: 1_762_299_278_440, from: OWNER, text: "Tell me more" });

describe("linkedinMessagesRecorder", () => {
  it("passes the page's request through and keeps the thread's messages, oldest first", async () => {
    const { window, pageFetch, respond } = loadRecorder();

    const res = await respond(MESSAGES_URL, syncResponse([REPLY, PITCH]));

    expect(pageFetch).toHaveBeenCalledWith(MESSAGES_URL, { credentials: "include" });
    expect(await res.json()).toEqual(syncResponse([REPLY, PITCH])); // the page still reads its body
    expect(window.__jhaLinkedInThread(THREAD)).toEqual({
      conversation_id: THREAD,
      counterpart_name: "Rita Recruiter",
      counterpart_profile_url: `https://www.linkedin.com/in/${RITA.id}`,
      messages: [
        { sent_at_ms: 1_762_298_888_637, from_owner: false, sender_name: "Rita Recruiter", subject: "Staff role", text: "Hi Pat, a Staff role?" },
        { sent_at_ms: 1_762_299_278_440, from_owner: true, sender_name: "Pat Seeker", subject: "Staff role", text: "Tell me more" },
      ],
    });
  });

  it("adds older pages and later syncs without repeating a message, and drops deleted ones", async () => {
    const { window, respond } = loadRecorder();
    const followUp = message({ id: "m3", at: 1_762_400_000_000, from: RITA, text: "Following up" });

    await respond(MESSAGES_URL, syncResponse([REPLY]));
    await respond(`${MESSAGES_URL}&older`, syncResponse([PITCH, REPLY]));
    await respond(`${MESSAGES_URL}&sync`, syncResponse([followUp], [REPLY.entityUrn]));

    expect(window.__jhaLinkedInThread(THREAD).messages.map((m) => m.text)).toEqual(["Hi Pat, a Staff role?", "Following up"]);
  });

  it("ignores LinkedIn's other requests", async () => {
    const { window, respond } = loadRecorder();
    await respond(
      "https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql?queryId=messengerConversations.0927f00f",
      syncResponse([PITCH]),
    );
    await respond("https://www.linkedin.com/voyager/api/graphql?queryId=messengerMessages.x", syncResponse([PITCH]));

    expect(window.__jhaLinkedInThread(THREAD)).toEqual({ error: "not_seen" });
  });

  it("refuses a thread with nothing from the other person, or more than one other person", async () => {
    const { window, respond } = loadRecorder();
    const groupThread = "2-R3JvdXA=";
    const mineOnly = "2-TWluZQ==";
    await respond(MESSAGES_URL, syncResponse([
      message({ id: "g1", at: 1, from: RITA, text: "Hi all", thread: groupThread }),
      message({ id: "g2", at: 2, from: GIL, text: "Hello", thread: groupThread }),
      message({ id: "o1", at: 3, from: OWNER, text: "Hey", thread: mineOnly }),
    ]));

    expect(window.__jhaLinkedInThread(groupThread)).toEqual({ error: "group" });
    expect(window.__jhaLinkedInThread(mineOnly)).toEqual({ error: "no_reply" });
  });

  it("still hands the page its response when the body isn't JSON", async () => {
    const { window, respond } = loadRecorder();

    const res = await respond(MESSAGES_URL, NOT_JSON);

    await expect(res.json()).rejects.toThrow(SyntaxError); // the page sees what it would have anyway
    expect(window.__jhaLinkedInThread(THREAD)).toEqual({ error: "not_seen" });
  });
});
