/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-24
// ai: claude-opus-5-5 | 2026-10-28 — generalized from useImportStatusChanges:
// one stream now carries a named event per topic (backend/user_events.py).
// Re-read a topic's endpoint when the server says it changed, instead of
// polling it for as long as the page is open. The server pushes a bare
// "changed" signal named after the topic over GET /api/events (server-sent
// events, routes/events.py); each topic's endpoint stays the only source of its
// data, so a dropped stream only delays a refresh -- every topic re-checks
// whenever the stream (re)connects, and a slow fallback timer covers the rest.
//
// One stream per browser, not per tab: the page is served over HTTP/1.1,
// where a browser allows only six connections per host across all its tabs,
// and a stream holds one open for good -- six open tabs would leave none for
// anything else. The tab holding the "jha-server-events" Web Lock owns the
// stream and relays each signal to the other tabs over a BroadcastChannel;
// when it closes, the lock passes to another tab, which opens the stream.
import { useEffect, useRef } from "react";
import { SERVER_EVENT_TOPICS, type ServerEventTopic } from "../types/common.ts"; // ai: claude-opus-5-5 | 2026-11-20

const STREAM_URL = "/api/events";
const LEADER_LOCK = "jha-server-events";
const RELAY_CHANNEL = "jha-server-events";
// Relayed to other tabs for a (re)connect, which concerns every topic.
const ALL_TOPICS = "*";
export const SERVER_EVENT_FALLBACK_MS = 60_000;

type Listener = () => void;
type RelayedSignal = ServerEventTopic | typeof ALL_TOPICS;

const listeners = new Map<ServerEventTopic, Set<Listener>>();
let stopShared: (() => void) | null = null;

function listenerCount(): number {
  let count = 0;
  for (const topicListeners of listeners.values()) count += topicListeners.size;
  return count;
}

function notifyListeners(signal: RelayedSignal): void {
  const topics = signal === ALL_TOPICS ? [...listeners.keys()] : [signal];
  for (const topic of topics) {
    for (const listener of [...(listeners.get(topic) ?? [])]) listener();
  }
}

/** Opens the stream; *onSignal* runs with the topic of every pushed event, and
 * with ALL_TOPICS on every (re)connect, since anything may have changed while
 * it was disconnected. */
function openStream(onSignal: (signal: RelayedSignal) => void): () => void {
  const source = new EventSource(STREAM_URL);
  for (const topic of SERVER_EVENT_TOPICS) source.addEventListener(topic, () => onSignal(topic));
  source.addEventListener("open", () => onSignal(ALL_TOPICS));
  return () => source.close();
}

function startShared(): () => void {
  if (typeof EventSource === "undefined") return () => {};
  if (typeof BroadcastChannel === "undefined" || !navigator.locks) {
    return openStream(notifyListeners);
  }
  const relay = new BroadcastChannel(RELAY_CHANNEL);
  relay.onmessage = (event: MessageEvent<RelayedSignal>) => notifyListeners(event.data);
  const abort = new AbortController();
  let closeStream: (() => void) | null = null;
  let releaseLeadership: (() => void) | null = null;
  navigator.locks
    .request(LEADER_LOCK, { signal: abort.signal }, () => {
      closeStream = openStream((signal) => {
        notifyListeners(signal);
        relay.postMessage(signal);
      });
      // Held until this tab stops listening (or closes, which releases it).
      return new Promise<void>((resolve) => { releaseLeadership = resolve; });
    })
    .catch(() => { /* aborted while waiting to lead */ });
  return () => {
    abort.abort();
    closeStream?.();
    releaseLeadership?.();
    relay.close();
  };
}

/** Calls *listener* each time *topic* may have changed. Returns the
 * unsubscribe function. */
export function subscribeServerEvents(topic: ServerEventTopic, listener: Listener): () => void {
  if (listenerCount() === 0) stopShared = startShared();
  const topicListeners = listeners.get(topic) ?? new Set<Listener>();
  topicListeners.add(listener);
  listeners.set(topic, topicListeners);
  return () => {
    topicListeners.delete(listener);
    if (topicListeners.size === 0) listeners.delete(topic);
    if (listenerCount() === 0) {
      stopShared?.();
      stopShared = null;
    }
  };
}

interface ServerEventOptions {
  enabled?: boolean;
  fallbackMs?: number;
}

/** While *enabled*, runs *refresh* whenever *topic* may have changed (a pushed
 * signal, a stream reconnect) and every *fallbackMs* regardless. Runs never
 * overlap: a signal that lands mid-refresh queues exactly one more run after
 * it, since signals can arrive in bursts (two per imported job). Callers do
 * their own first read; this only reports changes. */
export function useServerEvent(
  topic: ServerEventTopic,
  refresh: () => Promise<unknown> | void,
  { enabled = true, fallbackMs = SERVER_EVENT_FALLBACK_MS }: ServerEventOptions = {},
): void {
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  });

  useEffect(() => {
    if (!enabled) return undefined;
    let running = false;
    let again = false;
    let cancelled = false;
    const run = async () => {
      if (running) {
        again = true;
        return;
      }
      running = true;
      try {
        do {
          again = false;
          await refreshRef.current();
        } while (again && !cancelled);
      } finally {
        running = false;
      }
    };
    const unsubscribe = subscribeServerEvents(topic, run);
    const fallbackTimer = window.setInterval(run, fallbackMs);
    return () => {
      cancelled = true;
      unsubscribe();
      window.clearInterval(fallbackTimer);
    };
  }, [topic, enabled, fallbackMs]);
}
