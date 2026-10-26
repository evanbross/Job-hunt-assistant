/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-24
// Re-read GET /api/import/status when the server says it changed, instead of
// polling it every 1.2s for as long as the page is open. The server pushes a
// bare "changed" signal over GET /api/import/events (server-sent events,
// routes/import_.py); /api/import/status stays the only source of the status
// itself, so a dropped stream only delays a refresh -- the stream re-checks
// whenever it (re)connects, and a slow fallback timer covers the rest.
//
// One stream per browser, not per tab: the page is served over HTTP/1.1,
// where a browser allows only six connections per host across all its tabs,
// and a stream holds one open for good -- six open tabs would leave none for
// anything else. The tab holding the "jha-import-events" Web Lock owns the
// stream and relays each signal to the other tabs over a BroadcastChannel;
// when it closes, the lock passes to another tab, which opens the stream.
import { useEffect, useRef } from "react";

const STREAM_URL = "/api/import/events";
const LEADER_LOCK = "jha-import-events";
const RELAY_CHANNEL = "jha-import-events";
export const IMPORT_STATUS_FALLBACK_MS = 60_000;

type Listener = () => void;

const listeners = new Set<Listener>();
let stopShared: (() => void) | null = null;

function notifyListeners(): void {
  for (const listener of [...listeners]) listener();
}

/** Opens the stream; *onChange* runs on every signal and every (re)connect,
 * since anything may have changed while it was disconnected. */
function openStream(onChange: () => void): () => void {
  const source = new EventSource(STREAM_URL);
  source.addEventListener("import", onChange);
  source.addEventListener("open", onChange);
  return () => source.close();
}

function startShared(): () => void {
  if (typeof EventSource === "undefined") return () => {};
  if (typeof BroadcastChannel === "undefined" || !navigator.locks) {
    return openStream(notifyListeners);
  }
  const relay = new BroadcastChannel(RELAY_CHANNEL);
  relay.onmessage = notifyListeners;
  const abort = new AbortController();
  let closeStream: (() => void) | null = null;
  let releaseLeadership: (() => void) | null = null;
  navigator.locks
    .request(LEADER_LOCK, { signal: abort.signal }, () => {
      closeStream = openStream(() => {
        notifyListeners();
        relay.postMessage("changed");
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

/** Calls *listener* each time the caller's import status may have changed.
 * Returns the unsubscribe function. */
export function subscribeImportStatusChanges(listener: Listener): () => void {
  listeners.add(listener);
  if (listeners.size === 1) stopShared = startShared();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      stopShared?.();
      stopShared = null;
    }
  };
}

/** While *enabled*, runs *refresh* whenever the import status may have
 * changed (a pushed signal, a stream reconnect) and every
 * IMPORT_STATUS_FALLBACK_MS regardless. Runs never overlap: a signal that
 * lands mid-refresh queues exactly one more run after it, since progress
 * signals arrive in bursts (two per imported job). Callers do their own
 * first read; this only reports changes. */
export function useImportStatusChanges(refresh: () => Promise<unknown> | void, enabled = true): void {
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
    const unsubscribe = subscribeImportStatusChanges(run);
    const fallbackTimer = window.setInterval(run, IMPORT_STATUS_FALLBACK_MS);
    return () => {
      cancelled = true;
      unsubscribe();
      window.clearInterval(fallbackTimer);
    };
  }, [enabled]);
}
