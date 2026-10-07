import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/** Keep one editor mounted as it changes between an inline form and a dialog. */
export function HomeComposer({
  hasDraft,
  open,
  onOpen,
  onClose,
  inlineOnDesktop,
  children,
}: {
  hasDraft: boolean;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  inlineOnDesktop: boolean;
  children: ReactNode;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onOpen, onClose, hasDraft });
  callbacks.current = { onOpen, onClose, hasDraft };
  const modal = open;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const change = () => {
      // Resizing while typing must not hide the active draft or remount the IME.
      if (!inlineOnDesktop) return;
      if (media.matches && (callbacks.current.hasDraft || layer.current?.contains(document.activeElement))) {
        callbacks.current.onOpen();
      } else if (!media.matches) {
        callbacks.current.onClose();
      }
    };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, [inlineOnDesktop]);

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
      element.style.setProperty("--compose-viewport-height", `${viewport?.height ?? window.innerHeight}px`);
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
      element.style.removeProperty("--compose-viewport-height");
      document.body.style.overflow = previousOverflow;
      for (const [sibling, inert] of siblings) sibling.inert = inert;
      if (element.contains(document.activeElement) && document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
      const trigger = document.querySelector<HTMLButtonElement>(
        window.matchMedia("(max-width: 720px)").matches ? ".mobile-compose-button" : ".rail-post-button",
      );
      // Restore focus when the navigation is visible, rather than assuming its
      // transition has advanced after a fixed number of animation frames.
      const deadline = performance.now() + 300;
      const restoreFocus = () => {
        if (!trigger?.isConnected || trigger.closest("[inert]")) return;
        if (document.activeElement !== document.body && !element.contains(document.activeElement)) return;
        if (getComputedStyle(trigger).visibility === "visible") {
          trigger.focus({ preventScroll: true });
        } else if (performance.now() < deadline) {
          requestAnimationFrame(restoreFocus);
        }
      };
      requestAnimationFrame(restoreFocus);
    };
  }, [modal]);

  return (
    <div
      ref={layer}
      hidden={!inlineOnDesktop && !modal}
      className={`home-compose-layer${modal ? " modal-backdrop" : ""}`}
      onClick={(event) => {
        if (modal && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        id="post-composer"
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
  );
}
