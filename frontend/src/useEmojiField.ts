import { ChangeEvent, useEffect, useRef } from "react";

type Field = HTMLInputElement | HTMLTextAreaElement;

// Shared behavior for a text field that accepts emoji from the EmojiPicker.
// Tracks the caret while the field has focus so an emoji can be inserted at the
// cursor even after focus moves to the picker, respecting maxLength. Spread the
// returned `fieldProps` onto the <input>/<textarea> and pass `insertEmoji` to
// <EmojiPicker onSelect={...} />.
export function useEmojiField<T extends Field>(
  value: string,
  onValueChange: (next: string) => void,
  maxLength: number,
) {
  const ref = useRef<T>(null);
  const caretRef = useRef<{ start: number; end: number } | null>(null);

  // Auto-grow a <textarea> to fit its content: reset to the CSS min-height, then
  // lock the height to the content's scroll height. Runs on every render (no
  // dep array) because the floor isn't constant — attaching media relaxes the
  // CSS min-height, and the stale inline height must be re-measured then too,
  // not just on typing. Inputs are left untouched.
  useEffect(() => {
    const el = ref.current;
    if (!(el instanceof HTMLTextAreaElement)) {
      return;
    }
    // Own the sizing so a manual resize handle / scrollbar can't fight the grow.
    el.style.resize = "none";
    el.style.overflowY = "hidden";
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  });

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
    if (next.length > maxLength) {
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
  };

  return { insertEmoji, fieldProps };
}
