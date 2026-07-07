import { useEffect, useMemo, useRef, useState } from "react";
import { Clock, Search, Smile } from "lucide-react";
import { EMOJI_CATEGORIES, type EmojiItem } from "./emojiData";

const RECENTS_KEY = "emoji-recents";
const RECENTS_LIMIT = 24;

function loadRecents(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function saveRecents(recents: string[]) {
  try {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(recents));
  } catch {
    // Ignore storage failures (private mode, quota, etc.)
  }
}

// Some emojis intentionally live in more than one category (e.g. ⭐ 🔥 🌈).
// Deduplicate by char for the flat search index so every result has a unique
// React key, merging keywords so a term from either category still matches.
const ALL_EMOJIS: EmojiItem[] = (() => {
  const byChar = new Map<string, EmojiItem>();
  for (const category of EMOJI_CATEGORIES) {
    for (const emoji of category.emojis) {
      const existing = byChar.get(emoji.char);
      if (existing) {
        existing.name = `${existing.name} ${emoji.name}`;
      } else {
        byChar.set(emoji.char, { char: emoji.char, name: emoji.name });
      }
    }
  }
  return [...byChar.values()];
})();

export function EmojiPicker({ onSelect }: { onSelect: (emoji: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState(EMOJI_CATEGORIES[0].id);
  const [recents, setRecents] = useState<string[]>(loadRecents);
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    if (open) {
      setQuery("");
      searchRef.current?.focus();
    }
  }, [open]);

  const searchResults = useMemo(() => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) {
      return null;
    }
    return ALL_EMOJIS.filter((emoji) => emoji.name.includes(trimmed));
  }, [query]);

  function handlePick(emoji: string) {
    onSelect(emoji);
    setRecents((current) => {
      const next = [emoji, ...current.filter((item) => item !== emoji)].slice(0, RECENTS_LIMIT);
      saveRecents(next);
      return next;
    });
  }

  return (
    <div className="emoji-picker" ref={containerRef}>
      <button
        type="button"
        className="icon-button emoji-trigger"
        onClick={() => setOpen((value) => !value)}
        aria-label="Add emoji"
        aria-expanded={open}
        title="Add emoji"
      >
        <Smile size={20} aria-hidden="true" />
      </button>

      {open ? (
        <div className="emoji-panel" role="dialog" aria-label="Emoji picker">
          <label className="emoji-search">
            <Search size={16} aria-hidden="true" />
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search emojis"
              aria-label="Search emojis"
            />
          </label>

          {searchResults ? null : (
            <div className="emoji-tabs" role="tablist" aria-label="Emoji categories">
              {recents.length > 0 ? (
                <button
                  type="button"
                  className={activeCategory === "recent" ? "emoji-tab active" : "emoji-tab"}
                  onClick={() => setActiveCategory("recent")}
                  aria-label="Recent"
                  title="Recent"
                >
                  <Clock size={18} aria-hidden="true" />
                </button>
              ) : null}
              {EMOJI_CATEGORIES.map((category) => (
                <button
                  type="button"
                  key={category.id}
                  className={activeCategory === category.id ? "emoji-tab active" : "emoji-tab"}
                  onClick={() => setActiveCategory(category.id)}
                  aria-label={category.label}
                  title={category.label}
                >
                  <span aria-hidden="true">{category.icon}</span>
                </button>
              ))}
            </div>
          )}

          <div className="emoji-scroll">
            {searchResults ? (
              searchResults.length > 0 ? (
                <section className="emoji-section">
                  <h3>Search results</h3>
                  <div className="emoji-grid">
                    {searchResults.map((emoji) => (
                      <button
                        type="button"
                        key={emoji.char}
                        className="emoji-cell"
                        onClick={() => handlePick(emoji.char)}
                        aria-label={emoji.name}
                        title={emoji.name}
                      >
                        {emoji.char}
                      </button>
                    ))}
                  </div>
                </section>
              ) : (
                <p className="emoji-empty">No emojis found</p>
              )
            ) : activeCategory === "recent" ? (
              <section className="emoji-section">
                <h3>Recent</h3>
                <div className="emoji-grid">
                  {recents.map((char) => (
                    <button
                      type="button"
                      key={char}
                      className="emoji-cell"
                      onClick={() => handlePick(char)}
                      title="Recent emoji"
                    >
                      {char}
                    </button>
                  ))}
                </div>
              </section>
            ) : (
              EMOJI_CATEGORIES.filter((category) => category.id === activeCategory).map((category) => (
                <section className="emoji-section" key={category.id}>
                  <h3>{category.label}</h3>
                  <div className="emoji-grid">
                    {category.emojis.map((emoji) => (
                      <button
                        type="button"
                        key={emoji.char}
                        className="emoji-cell"
                        onClick={() => handlePick(emoji.char)}
                        aria-label={emoji.name}
                        title={emoji.name}
                      >
                        {emoji.char}
                      </button>
                    ))}
                  </div>
                </section>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
