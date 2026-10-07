import { useEffect, useRef, useState } from "react";

const THRESHOLD = 64;
const MAX_PULL = 88;
const EXCLUDED =
  "a, button, input, textarea, select, [contenteditable], [role=dialog], .composer, video";

/** A vertical, one-finger pull that starts at the top of the mobile timeline. */
export function usePullToRefresh({
  disabled,
  onRefresh,
  resetKey,
}: {
  disabled: boolean;
  onRefresh: () => void;
  resetKey: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [distance, setDistance] = useState(0);
  const latest = useRef({ disabled, onRefresh });
  latest.current = { disabled, onRefresh };

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setDistance(0);
    const mobile = window.matchMedia(
      "(max-width: 720px) and (pointer: coarse)",
    );
    let start: { x: number; y: number } | null = null;
    let pulling = false;
    let pulled = 0;
    const reset = () => {
      start = null;
      pulling = false;
      pulled = 0;
      setDistance(0);
    };
    const onStart = (event: TouchEvent) => {
      reset();
      if (
        latest.current.disabled ||
        !mobile.matches ||
        event.touches.length !== 1 ||
        window.scrollY > 0 ||
        (event.target instanceof Element && event.target.closest(EXCLUDED)) ||
        document.activeElement?.matches("input, textarea, [contenteditable]") ||
        window.getSelection()?.isCollapsed === false
      )
        return;
      start = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    };
    const onMove = (event: TouchEvent) => {
      if (!start) return;
      if (
        latest.current.disabled ||
        event.touches.length !== 1 ||
        !mobile.matches
      ) {
        reset();
        return;
      }
      const dx = event.touches[0].clientX - start.x;
      const dy = event.touches[0].clientY - start.y;
      if (!pulling) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
        if (
          dy <= 0 ||
          Math.abs(dx) >= dy ||
          window.scrollY > 0 ||
          !event.cancelable
        ) {
          reset();
          return;
        }
        pulling = true;
      }
      if (event.cancelable) event.preventDefault();
      pulled = Math.min(MAX_PULL, Math.max(0, dy * 0.5));
      setDistance(pulled);
    };
    const onEnd = () => {
      const refresh =
        pulling && pulled >= THRESHOLD && !latest.current.disabled;
      reset();
      if (refresh) latest.current.onRefresh();
    };
    element.addEventListener("touchstart", onStart, { passive: true });
    element.addEventListener("touchmove", onMove, { passive: false });
    element.addEventListener("touchend", onEnd);
    element.addEventListener("touchcancel", reset);
    mobile.addEventListener("change", reset);
    return () => {
      element.removeEventListener("touchstart", onStart);
      element.removeEventListener("touchmove", onMove);
      element.removeEventListener("touchend", onEnd);
      element.removeEventListener("touchcancel", reset);
      mobile.removeEventListener("change", reset);
    };
  }, [resetKey]);

  return { ref, distance, ready: distance >= THRESHOLD };
}
