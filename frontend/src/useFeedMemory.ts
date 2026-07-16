import { useCallback, useEffect, useRef } from "react";
import { useNavigationType } from "react-router-dom";

// Snapshots of what each list view had loaded, keyed by the view's identity
// ("home:for-you", "hashtag:rust:top", …). Session-scoped, in memory — the
// counterpart of ScrollMemory's saved offsets.
const snapshots = new Map<string, unknown>();

/**
 * Back/forward content memory for a paginated feed. Restoring a scroll offset
 * is useless if the content it pointed into is gone (a fresh fetch only loads
 * the first page); this hook re-hydrates the exact list the user left —
 * including "Load more" pages — when they come *back* to it.
 *
 * The `snapshot` is saved when `key` changes or the view unmounts (skipped
 * while `empty`). Call `take()` inside the load effect: it returns the saved
 * snapshot when this navigation arrived via back/forward (POP), else null —
 * hydrate state from it and skip fetching. Fresh visits still fetch.
 */
export function useFeedMemory<T>(key: string, snapshot: T, empty: boolean): () => T | null {
  const navigationType = useNavigationType();
  // Ref, so the save-on-leave effect below reads current data without having
  // to re-subscribe (and re-save) on every state change.
  const latest = useRef({ snapshot, empty });
  latest.current = { snapshot, empty };

  useEffect(() => {
    return () => {
      if (!latest.current.empty) {
        snapshots.set(key, latest.current.snapshot);
      }
    };
  }, [key]);

  return useCallback((): T | null => {
    if (navigationType !== "POP") {
      return null;
    }
    return (snapshots.get(key) as T | undefined) ?? null;
  }, [key, navigationType]);
}
