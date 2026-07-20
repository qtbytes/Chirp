import { useCallback, useEffect, useRef, useState } from "react";

type UseInfiniteScrollArgs = {
  /** Whether another page exists to fetch. */
  hasMore: boolean;
  /** Whether a fetch is already in flight (guards against double-firing). */
  loading: boolean;
  /** Fetch the next page. Called at most once per time the sentinel appears. */
  onLoadMore: () => void;
  /**
   * How far before the sentinel actually reaches the viewport to start loading.
   * A generous margin means the next page is usually there by the time the user
   * scrolls to it, so the feed feels seamless rather than stop-and-go.
   */
  rootMargin?: string;
};

/**
 * Auto-load-more via IntersectionObserver: attach the returned ref to a sentinel
 * element at the end of a list, and ``onLoadMore`` fires whenever that sentinel
 * scrolls into view while there is more to load.
 *
 * The trigger is modelled as state (``visible``) rather than fired straight from
 * the observer callback, which makes it self-chaining: after a page loads,
 * ``loading`` flips back to false and, if the sentinel is *still* in view (a
 * short page that didn't fill the viewport), the effect runs again and pulls the
 * next one -- until the content finally pushes the sentinel out of range.
 */
export function useInfiniteScroll({
  hasMore,
  loading,
  onLoadMore,
  rootMargin = "600px",
}: UseInfiniteScrollArgs): (node: HTMLElement | null) => void {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!node || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0]?.isIntersecting ?? false),
      { rootMargin },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, rootMargin]);

  // Keep the latest callback without making it an effect dependency, so a fresh
  // closure each render (the usual ``() => load(cursor, true)``) doesn't re-run
  // the observer wiring.
  const onLoadMoreRef = useRef(onLoadMore);
  useEffect(() => {
    onLoadMoreRef.current = onLoadMore;
  });

  useEffect(() => {
    if (visible && hasMore && !loading) {
      onLoadMoreRef.current();
    }
  }, [visible, hasMore, loading]);

  return useCallback((next: HTMLElement | null) => setNode(next), []);
}

type InfiniteScrollProps = {
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
};

/**
 * Drop-in replacement for a "Load more" button: an invisible sentinel that pulls
 * the next page as it nears the viewport. Renders nothing once the list is
 * exhausted. The in-flight indicator stays each view's own ``loading`` spinner,
 * so nothing here competes with it.
 */
export function InfiniteScroll({
  hasMore,
  loading,
  onLoadMore,
}: InfiniteScrollProps) {
  const sentinelRef = useInfiniteScroll({ hasMore, loading, onLoadMore });
  if (!hasMore) {
    return null;
  }
  return (
    <div
      ref={sentinelRef}
      className="infinite-scroll-sentinel"
      aria-hidden="true"
    />
  );
}
