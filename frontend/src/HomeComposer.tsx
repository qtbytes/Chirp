import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Feather, X } from "lucide-react";
import { Avatar } from "./components";
import type { UserSummary } from "./types";

/** Keep one editor mounted as it changes between an inline form and a dialog. */
export function HomeComposer({
  currentUser,
  hasDraft,
  open,
  onOpen,
  onClose,
  children,
}: {
  currentUser: UserSummary;
  hasDraft: boolean;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 720px)").matches);
  const layer = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const callbacks = useRef({ onOpen, onClose, hasDraft });
  callbacks.current = { onOpen, onClose, hasDraft };
  const id = useId();
  const modal = mobile && open;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const change = () => {
      // Resizing while typing must not hide the active draft or remount the IME.
      if (media.matches && (callbacks.current.hasDraft || layer.current?.contains(document.activeElement))) {
        callbacks.current.onOpen();
      } else if (!media.matches) {
        callbacks.current.onClose();
      }
      setMobile(media.matches);
    };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    if (!modal) return;
    const element = layer.current!;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Disable the page around the editor, while allowing its portaled emoji
    // and mention panels to remain interactive.
    const siblings: [HTMLElement, boolean][] = [];
    for (let node: HTMLElement | null = element; node && node !== document.body; node = node.parentElement) {
      for (const sibling of node.parentElement?.children ?? []) {
        if (sibling !== node && sibling instanceof HTMLElement) {
          siblings.push([sibling, sibling.inert]);
          sibling.inert = true;
        }
      }
    }
    const field = element.querySelector<HTMLTextAreaElement>("textarea");
    field?.focus({ preventScroll: true });

    const viewport = window.visualViewport;
    const size = () => {
      element.style.height = `${viewport?.height ?? window.innerHeight}px`;
      element.style.top = `${viewport?.offsetTop ?? 0}px`;
    };
    size();
    viewport?.addEventListener("resize", size);
    viewport?.addEventListener("scroll", size);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        // Nested pickers own Escape until they close.
        if (document.querySelector(".emoji-panel, .visibility-menu, .alt-modal, .typeahead-menu")) {
          // A portaled picker's focused search field disappears when it closes.
          requestAnimationFrame(() => {
            if (document.activeElement === document.body && element.classList.contains("modal-backdrop")) {
              field?.focus({ preventScroll: true });
            }
          });
          return;
        }
        event.preventDefault();
        callbacks.current.onClose();
      } else if (event.key === "Tab") {
        const controls = Array.from(document.querySelectorAll<HTMLElement>(
          'button, a[href], input, textarea, select, [tabindex="0"]',
        )).filter((el) => !el.closest("[inert]") && !el.matches(":disabled") && el.getClientRects().length > 0);
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    // Capture before a picker's Escape handler removes it from the DOM.
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      viewport?.removeEventListener("resize", size);
      viewport?.removeEventListener("scroll", size);
      element.style.removeProperty("height");
      element.style.removeProperty("top");
      document.body.style.overflow = previousOverflow;
      for (const [sibling, inert] of siblings) sibling.inert = inert;
      if (trigger.current?.isConnected && window.matchMedia("(max-width: 720px)").matches) {
        trigger.current.focus({ preventScroll: true });
      }
    };
  }, [modal]);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="mobile-compose-entry"
        aria-label={hasDraft ? "Continue your draft" : "Compose post"}
        aria-haspopup="dialog"
        aria-expanded={modal}
        aria-controls={id}
        onClick={onOpen}
      >
        <Avatar user={currentUser} size="small" />
        <span className="mobile-compose-prompt">{hasDraft ? "Continue your draft" : "What is happening?"}</span>
        {hasDraft ? <span className="mobile-compose-draft">Draft</span> : null}
        <Feather size={19} className="mobile-compose-icon" aria-hidden="true" />
      </button>
      <div
        ref={layer}
        className={`home-compose-layer${modal ? " modal-backdrop" : ""}`}
        onClick={(event) => {
          if (modal && event.target === event.currentTarget) onClose();
        }}
      >
        <div
          id={id}
          className={modal ? "compose-modal home-compose-dialog" : undefined}
          role={modal ? "dialog" : undefined}
          aria-modal={modal || undefined}
          aria-label={modal ? "Compose post" : undefined}
        >
          <div className="home-compose-heading">
            <button type="button" className="icon-button" aria-label="Close composer" onClick={onClose}>
              <X size={20} aria-hidden="true" />
            </button>
            <strong>New post</strong>
          </div>
          {children}
        </div>
      </div>
    </>
  );
}
