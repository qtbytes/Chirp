import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { Smile } from "lucide-react";

// The panel — and the large generated emoji dataset it imports — is code-split
// out of the main bundle and fetched the first time any picker is opened.
const EmojiPanel = lazy(() => import("./EmojiPanel"));

// The panel's full height (search + tabs + scroll area + padding). Used to
// decide whether it still fits below the trigger or must open upward.
const PANEL_HEIGHT = 400;

export function EmojiPicker({ onSelect }: { onSelect: (emoji: string) => void }) {
  const [open, setOpen] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div className="emoji-picker" ref={containerRef}>
      <button
        type="button"
        className="icon-button emoji-trigger"
        onClick={() => {
          if (!open) {
            // A tall composer pushes the trigger toward the bottom of the
            // screen; open the panel upward when it no longer fits below.
            const rect = containerRef.current?.getBoundingClientRect();
            setOpenUp(Boolean(rect && window.innerHeight - rect.bottom < PANEL_HEIGHT));
          }
          setOpen((value) => !value);
        }}
        aria-label="Add emoji"
        aria-expanded={open}
        title="Add emoji"
      >
        <Smile size={20} aria-hidden="true" />
      </button>

      {open ? (
        <Suspense
          fallback={
            <div
              className={openUp ? "emoji-panel emoji-panel--up" : "emoji-panel"}
              role="dialog"
              aria-label="Emoji picker"
            />
          }
        >
          <EmojiPanel openUp={openUp} onPick={onSelect} />
        </Suspense>
      ) : null}
    </div>
  );
}
