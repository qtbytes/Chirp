import { CSSProperties, Suspense, lazy, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Smile } from "lucide-react";

// The panel interior — and the large generated emoji dataset it imports — is
// code-split out of the main bundle and fetched the first time any picker is
// opened. The shell div stays here so it can render instantly (and hold the
// ref the outside-click handler needs) while the chunk loads.
const EmojiPanel = lazy(() => import("./EmojiPanel"));

// The panel's full height (search + tabs + scroll area + padding). Used to
// decide whether it fits below the trigger or must open upward.
const PANEL_HEIGHT = 400;

export function EmojiPicker({ onSelect }: { onSelect: (emoji: string) => void }) {
  const [open, setOpen] = useState(false);
  // Viewport coordinates: the panel renders in a portal with position:fixed,
  // so a dialog composer's overflow can never clip it — opening upward from a
  // trigger at the bottom of a modal used to cut the search bar and tabs off
  // at the modal's top edge.
  const [anchor, setAnchor] = useState<CSSProperties | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    function isInside(target: EventTarget | null) {
      return (
        target instanceof Node &&
        Boolean(containerRef.current?.contains(target) || panelRef.current?.contains(target))
      );
    }

    function handlePointerDown(event: MouseEvent) {
      if (!isInside(event.target)) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    // The fixed-position panel would be left floating if the page scrolled
    // under it; close instead (scrolls *inside* the panel are its own).
    function handleScroll(event: Event) {
      if (event.target instanceof Node && panelRef.current?.contains(event.target)) {
        return;
      }
      setOpen(false);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", handleScroll);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", handleScroll);
    };
  }, [open]);

  function toggleOpen() {
    if (!open) {
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        const width = Math.min(340, window.innerWidth - 16);
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
        // Below the trigger when it fits; otherwise pinned above it. Fixed
        // coordinates, so neither direction can be clipped by a dialog.
        setAnchor(
          window.innerHeight - rect.bottom >= PANEL_HEIGHT + 16
            ? { top: rect.bottom + 10, left }
            : { bottom: window.innerHeight - rect.top + 10, left },
        );
      } else {
        setAnchor(null);
      }
    }
    setOpen((value) => !value);
  }

  return (
    <div className="emoji-picker" ref={containerRef}>
      <button
        type="button"
        className="icon-button emoji-trigger"
        onClick={toggleOpen}
        aria-label="Add emoji"
        aria-expanded={open}
        title="Add emoji"
      >
        <Smile size={20} aria-hidden="true" />
      </button>

      {open
        ? createPortal(
            <div
              ref={panelRef}
              className="emoji-panel emoji-panel--floating"
              style={anchor ?? undefined}
              role="dialog"
              aria-label="Emoji picker"
            >
              <Suspense fallback={null}>
                <EmojiPanel onPick={onSelect} />
              </Suspense>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
