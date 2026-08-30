import { ChangeEvent, useLayoutEffect, useRef } from "react";

type Field = HTMLInputElement | HTMLTextAreaElement;

// Shared behavior for a text field that accepts emoji from the EmojiPicker.
// Tracks the caret while the field has focus so an emoji can be inserted at the
// cursor even after focus moves to the picker. Spread the returned `fieldProps`
// onto the <input>/<textarea> and pass `insertEmoji` to
// <EmojiPicker onSelect={...} />.
//
// `maxLength` is for fields with a *hard* cap (the field also carries the
// attribute), where an emoji that would overflow is simply dropped. Post
// composers leave it off: they let the draft run past their limit and mark the
// overflow red instead.
export function useEmojiField<T extends Field>(
  value: string,
  onValueChange: (next: string) => void,
  maxLength?: number,
) {
  const ref = useRef<T>(null);
  const caretRef = useRef<{ start: number; end: number } | null>(null);
  const composingRef = useRef(false);
  const resizeFrameRef = useRef<number | null>(null);

  // A fractional line-height can leave scrollHeight one pixel short at a wrap
  // boundary. The extra pixels keep the final glyph inside the mirrored layer.
  const TEXTAREA_HEIGHT_BUFFER = 2;

  function resizeTextarea() {
    const el = ref.current;
    if (!(el instanceof HTMLTextAreaElement)) {
      return;
    }
    // Own the sizing so a manual resize handle / scrollbar can't fight the grow.
    el.style.resize = "none";
    el.style.overflowY = "hidden";
    el.style.height = "auto";
    el.style.height = `${Math.ceil(el.scrollHeight) + TEXTAREA_HEIGHT_BUFFER}px`;
  }

  function scheduleResizeTextarea() {
    if (composingRef.current || resizeFrameRef.current !== null) {
      return;
    }
    resizeFrameRef.current = requestAnimationFrame(() => {
      resizeFrameRef.current = null;
      resizeTextarea();
    });
  }

  // Run before paint so inserting media cannot briefly display the stale
  // textarea height and clip the last line in .composer-highlight. This runs
  // on every render because attaching media changes the CSS sizing floor.
  useLayoutEffect(() => {
    if (!composingRef.current) {
      resizeTextarea();
    }
  });

  // A modal scrollbar can appear after the first measurement and make the
  // textarea narrower. That rewraps the draft without a React render, leaving
  // scrollHeight larger than the inline height. Observe width changes so the
  // new line count is measured immediately. Font loading can cause the same
  // post-mount reflow, so measure once after the document fonts settle too.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!(el instanceof HTMLTextAreaElement)) {
      return;
    }

    let disposed = false;
    let lastWidth = el.getBoundingClientRect().width;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - lastWidth) < 0.5) {
        return;
      }
      lastWidth = width;
      scheduleResizeTextarea();
    });
    observer.observe(el);

    void document.fonts?.ready.then(() => {
      if (!disposed) {
        scheduleResizeTextarea();
      }
    });

    return () => {
      disposed = true;
      observer.disconnect();
      if (resizeFrameRef.current !== null) {
        cancelAnimationFrame(resizeFrameRef.current);
        resizeFrameRef.current = null;
      }
    };
  }, []);

  function handleCompositionStart() {
    composingRef.current = true;
  }

  function handleCompositionEnd() {
    composingRef.current = false;
    // Let the browser commit the composed text before measuring its final wrap.
    scheduleResizeTextarea();
  }

  function rememberCaret() {
    const el = ref.current;
    if (el) {
      caretRef.current = {
        start: el.selectionStart ?? value.length,
        end: el.selectionEnd ?? value.length,
      };
    }
  }

  function handleChange(event: ChangeEvent<T>) {
    onValueChange(event.target.value);
    rememberCaret();
  }

  function insertEmoji(emoji: string) {
    const el = ref.current;
    // Reading el.selectionStart here is unreliable because focus has moved to the
    // picker, so use the caret captured while the field last had focus.
    const { start, end } = caretRef.current ?? { start: value.length, end: value.length };
    const next = value.slice(0, start) + emoji + value.slice(end);
    if (maxLength !== undefined && next.length > maxLength) {
      return;
    }
    const caret = start + emoji.length;
    caretRef.current = { start: caret, end: caret };
    onValueChange(next);
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    });
  }

  const fieldProps = {
    ref,
    onChange: handleChange,
    onSelect: rememberCaret,
    onClick: rememberCaret,
    onKeyUp: rememberCaret,
    onCompositionStart: handleCompositionStart,
    onCompositionEnd: handleCompositionEnd,
  };

  return { insertEmoji, fieldProps };
}
