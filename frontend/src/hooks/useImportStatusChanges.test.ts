/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-24
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Handler = () => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  private handlers = new Map<string, Handler[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: Handler): void {
    this.handlers.set(type, [...(this.handlers.get(type) ?? []), handler]);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string): void {
    if (!this.closed) for (const handler of this.handlers.get(type) ?? []) handler();
  }

  static open(): FakeEventSource[] {
    return FakeEventSource.instances.filter((source) => !source.closed);
  }
}

/** Same-name channels in one realm, like tabs of one browser: a message goes to
 * every other instance, never back to the sender. */
class FakeBroadcastChannel {
  static all: FakeBroadcastChannel[] = [];
  onmessage: Handler | null = null;
  readonly name: string;

  constructor(name: string) {
    this.name = name;
    FakeBroadcastChannel.all.push(this);
  }

  postMessage(): void {
    for (const other of FakeBroadcastChannel.all) {
      if (other !== this && other.name === this.name) other.onmessage?.();
    }
  }

  close(): void {
    FakeBroadcastChannel.all = FakeBroadcastChannel.all.filter((channel) => channel !== this);
  }
}

/** One exclusive lock, granted in request order; released when the holder's
 * callback promise settles; a queued request is withdrawn by its abort signal. */
class FakeLockManager {
  private held = false;
  private waiting: Array<() => void> = [];

  request(_name: string, options: { signal?: AbortSignal }, callback: () => Promise<void>): Promise<void> {
    return new Promise((resolve, reject) => {
      const grant = () => {
        this.held = true;
        callback().finally(() => {
          this.held = false;
          this.waiting.shift()?.();
          resolve();
        });
      };
      options.signal?.addEventListener("abort", () => {
        if (this.waiting.includes(grant)) {
          this.waiting = this.waiting.filter((queued) => queued !== grant);
          reject(new DOMException("Aborted", "AbortError"));
        }
      });
      if (this.held) this.waiting.push(grant);
      else grant();
    });
  }
}

type EventsModule = typeof import("./useImportStatusChanges.ts");

/** A fresh copy of the module: what each tab of the browser has its own of. */
async function loadTab(): Promise<EventsModule> {
  vi.resetModules();
  return import("./useImportStatusChanges.ts");
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe("import status changes", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    FakeBroadcastChannel.all = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    Object.defineProperty(navigator, "locks", { value: new FakeLockManager(), configurable: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    Reflect.deleteProperty(navigator, "locks");
  });

  it("signals on each pushed change and each (re)connect, and closes the stream with its last listener", async () => {
    const tab = await loadTab();
    const listener = vi.fn();
    const unsubscribe = tab.subscribeImportStatusChanges(listener);
    await flush();

    const [source] = FakeEventSource.open();
    expect(source.url).toBe("/api/import/events");
    source.emit("open");
    source.emit("import");
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    expect(source.closed).toBe(true);
  });

  it("opens one stream for every tab, relays its signals, and hands it over when its tab closes", async () => {
    const first = await loadTab();
    const second = await loadTab();
    const firstListener = vi.fn();
    const secondListener = vi.fn();
    const unsubscribeFirst = first.subscribeImportStatusChanges(firstListener);
    second.subscribeImportStatusChanges(secondListener);
    await flush();

    expect(FakeEventSource.open()).toHaveLength(1);
    FakeEventSource.open()[0].emit("import");
    expect(firstListener).toHaveBeenCalledTimes(1);
    expect(secondListener).toHaveBeenCalledTimes(1);

    unsubscribeFirst();
    await flush();
    const open = FakeEventSource.open();
    expect(open).toHaveLength(1);  // the second tab took over
    open[0].emit("import");
    expect(secondListener).toHaveBeenCalledTimes(2);
    expect(firstListener).toHaveBeenCalledTimes(1);
  });

  it("opens a stream per tab where Web Locks aren't available", async () => {
    Reflect.deleteProperty(navigator, "locks");
    const first = await loadTab();
    const second = await loadTab();
    first.subscribeImportStatusChanges(vi.fn());
    second.subscribeImportStatusChanges(vi.fn());
    expect(FakeEventSource.open()).toHaveLength(2);
  });

  it("never overlaps refreshes: a burst of signals mid-refresh runs exactly one more", async () => {
    const tab = await loadTab();
    let finish: () => void = () => {};
    const refresh = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    renderHook(() => tab.useImportStatusChanges(refresh));
    await flush();
    const [source] = FakeEventSource.open();

    act(() => source.emit("import"));
    act(() => { source.emit("import"); source.emit("import"); source.emit("import"); });
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => { finish(); });
    await flush();
    expect(refresh).toHaveBeenCalledTimes(2);
    await act(async () => { finish(); });
    await flush();
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("re-checks on the fallback timer even with no signal, and not at all while disabled", async () => {
    vi.useFakeTimers();
    const tab = await loadTab();
    const refresh = vi.fn();
    const { rerender } = renderHook(({ enabled }) => tab.useImportStatusChanges(refresh, enabled), {
      initialProps: { enabled: false },
    });
    act(() => { vi.advanceTimersByTime(tab.IMPORT_STATUS_FALLBACK_MS); });
    expect(refresh).not.toHaveBeenCalled();
    expect(FakeEventSource.open()).toHaveLength(0);

    rerender({ enabled: true });
    act(() => { vi.advanceTimersByTime(tab.IMPORT_STATUS_FALLBACK_MS); });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
