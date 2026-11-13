/*!
 * Copyright (c) 2026 ZombieTech LLC. All rights reserved.
 *
 * SPDX-License-Identifier: PolyForm-Strict-1.0.0
 * See LICENSE in the project root for the full terms.
 *
 * Licensing inquiries: license@zombietech.biz
 */

// ai: claude-opus-5-5 | 2026-10-23
// Sits after a paged list (useJobQueueListing.ts) and asks for the next page
// as it nears the viewport. The observer is rebuilt whenever the item count
// changes, because an IntersectionObserver only reports *changes*: if a page
// was too short to push the sentinel off-screen, the fresh observer's initial
// report is what asks for the page after it. The button is the fallback for
// a browser without IntersectionObserver, or one that never scrolls.
import { useEffect, useRef } from "react";

interface LoadMoreSentinelProps {
  hasMore: boolean;
  loading: boolean;
  itemCount: number;
  onLoadMore: () => void;
}

export default function LoadMoreSentinel({ hasMore, loading, itemCount, onLoadMore }: LoadMoreSentinelProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!hasMore || !el || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
      },
      { rootMargin: "800px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, itemCount, onLoadMore]);

  if (!hasMore) return null;
  return (
    <div ref={ref} style={{ display: "flex", justifyContent: "center", padding: "16px 0" }}>
      {loading ? (
        <span className="inline-spinner" style={{ color: "var(--accent)", width: "20px", height: "20px" }} aria-label="Loading more jobs" />
      ) : (
        <button className="btn-secondary" onClick={onLoadMore} style={{ fontSize: "13px", padding: "6px 14px" }}>
          Load more
        </button>
      )}
    </div>
  );
}
