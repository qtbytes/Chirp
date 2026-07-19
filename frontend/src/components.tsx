import {
  FormEvent,
  KeyboardEvent,
  RefObject,
  TouchEvent,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import {
  Ban,
  BarChart2,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Flag,
  Globe,
  Heart,
  Image as ImageIcon,
  Loader2,
  Lock,
  MessageCircle,
  MoreHorizontal,
  MoreVertical,
  Pencil,
  Repeat2,
  Share2,
  Trash2,
  Users,
  VolumeX,
  X,
} from "lucide-react";
import {
  ApiError,
  blockUser,
  createComment,
  createTweet,
  deleteComment,
  deleteTweet,
  displayName,
  muteUser,
  editComment,
  editTweet,
  isVideoUrl,
  listUsers,
  recordPostViews,
  replyToComment,
  reportPost,
  reportUser,
  resolveMediaUrl,
  suggestHashtags,
  toggleCommentLike,
  toggleTweetLike,
  unfurlUrl,
} from "./api";
import type {
  Comment,
  CommentStats,
  LinkPreview,
  QuotedPost,
  ReportReason,
  TrendingHashtag,
  Tweet,
  TweetStats,
  TweetVisibility,
  UserDiscovery,
  UserSummary,
} from "./types";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import {
  ACCEPTED_MEDIA,
  MAX_ALT_LENGTH,
  useMediaAttachment,
  type MediaAttachment,
  type MediaItem,
} from "./useMediaAttachment";

/**
 * The signed-in user. Provided once by App so leaf components (the reply
 * composer needs the avatar) don't have to be threaded through every card.
 */
const CurrentUserContext = createContext<UserSummary | null>(null);

export const CurrentUserProvider = CurrentUserContext.Provider;

export function useCurrentUser(): UserSummary | null {
  return useContext(CurrentUserContext);
}

export function Avatar({
  user,
  size = "regular",
}: {
  user: UserSummary;
  size?: "small" | "regular" | "large";
}) {
  const src = resolveMediaUrl(user.avatar_url);
  const sizeClass =
    size === "small" ? "avatar small" : size === "large" ? "avatar large" : "avatar";

  if (src) {
    return <img className={`${sizeClass} avatar-image`} src={src} alt="" aria-hidden="true" />;
  }
  return (
    <div className={sizeClass} aria-hidden="true">
      {user.username.slice(0, 1).toUpperCase()}
    </div>
  );
}

const URL_REGEX = /https?:\/\/[^\s]+/g;
const IMAGE_EXTENSION = /\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i;
// Trailing punctuation that is almost always sentence punctuation, not URL.
const TRAILING_PUNCTUATION = /[.,!?;:)\]}'"]+$/;

function isImageUrl(url: string): boolean {
  try {
    return IMAGE_EXTENSION.test(new URL(url).pathname);
  } catch {
    return false;
  }
}

/**
 * The first URL in some text worth unfurling into a preview card — i.e. an
 * http(s) link that isn't itself an image or video (those already render
 * inline). Returns null when there is none. Mirrors Twitter: one card per post.
 */
export function firstPreviewableUrl(text: string): string | null {
  const matches = text.match(URL_REGEX);
  if (!matches) {
    return null;
  }
  for (const raw of matches) {
    const url = raw.replace(TRAILING_PUNCTUATION, "");
    if (isImageUrl(url) || isVideoUrl(url)) {
      continue;
    }
    return url;
  }
  return null;
}

type ContentToken = { type: "text" | "url" | "hashtag" | "mention"; value: string };

// One pass over URLs, #hashtags and @mentions. URL is the first alternative so a
// link's own "#fragment"/"@userinfo" is swallowed whole and not mistaken for an
// entity. The hashtag/mention bodies mirror the backend extraction rules
// (app/services/text_entities.py): word chars for tags, the username charset for
// mentions, each only at a non-word boundary so "a@b" or "c#d" mid-word do not
// count.
const ENTITY_REGEX =
  /(https?:\/\/[^\s]+)|(?<!\w)(#\w{1,140})|(?<!\w)(@[A-Za-z0-9_]{1,50})/g;

function tokenizeContent(text: string): ContentToken[] {
  const tokens: ContentToken[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(ENTITY_REGEX)) {
    const start = match.index ?? 0;
    const [full, urlMatch, hashtagMatch, mentionMatch] = match;
    if (start > lastIndex) {
      tokens.push({ type: "text", value: text.slice(lastIndex, start) });
    }
    if (urlMatch !== undefined) {
      let url = urlMatch;
      // Keep trailing punctuation as plain text so "see http://x.com." works.
      const trailing = url.match(TRAILING_PUNCTUATION)?.[0] ?? "";
      if (trailing) {
        url = url.slice(0, url.length - trailing.length);
      }
      tokens.push({ type: "url", value: url });
      if (trailing) {
        tokens.push({ type: "text", value: trailing });
      }
    } else if (hashtagMatch !== undefined) {
      tokens.push({ type: "hashtag", value: hashtagMatch });
    } else if (mentionMatch !== undefined) {
      tokens.push({ type: "mention", value: mentionMatch });
    }
    lastIndex = start + full.length;
  }
  if (lastIndex < text.length) {
    tokens.push({ type: "text", value: text.slice(lastIndex) });
  }
  return tokens;
}

/**
 * Twitter-style entity coloring for a draft being typed: URLs, #hashtags and
 * @mentions. Rendered as a backdrop layer inside `.composer-input`, behind a
 * transparent-text <textarea>, so entities read highlighted while staying
 * plain editable text — not clickable.
 * Both layers must share identical text metrics (see the CSS) or they drift.
 */
export function ComposerHighlight({ text }: { text: string }) {
  const tokens = useMemo(() => tokenizeContent(text), [text]);
  return (
    <div className="composer-highlight" aria-hidden="true">
      {tokens.map((token, index) =>
        token.type === "text" ? (
          token.value
        ) : (
          <span key={index} className="composer-highlight-url">
            {token.value}
          </span>
        ),
      )}
    </div>
  );
}

/** The partial `@mention` / `#hashtag` the caret is currently inside. */
type TypeaheadEntity = {
  type: "mention" | "hashtag";
  /** The body typed so far, without the trigger character. */
  query: string;
  /** Index of the `@`/`#` trigger in the draft. */
  start: number;
  /** End of the word around the caret, so accepting mid-word replaces all of it. */
  end: number;
};

// Anchored at the caret ($) and mirroring the tokenizer's boundary rules, so
// the menu opens exactly where the highlight layer would color an entity.
const MENTION_AT_CARET = /(?<!\w)@([A-Za-z0-9_]{0,50})$/;
const HASHTAG_AT_CARET = /(?<!\w)#(\w{0,140})$/u;

function findTypeaheadEntity(text: string, caret: number): TypeaheadEntity | null {
  const before = text.slice(0, caret);
  const mention = before.match(MENTION_AT_CARET);
  const match = mention ?? before.match(HASHTAG_AT_CARET);
  if (!match || match.index === undefined) {
    return null;
  }
  const type = mention ? "mention" : "hashtag";
  const wordChar = type === "mention" ? /[A-Za-z0-9_]/ : /\w/u;
  let end = caret;
  while (end < text.length && wordChar.test(text[end])) {
    end += 1;
  }
  return { type, query: match[1], start: match.index, end };
}

/**
 * A Range over the character at ``offset`` inside the highlight backdrop.
 *
 * The backdrop renders the draft with the exact metrics of the textarea, so
 * this rect *is* where that character sits in the textarea — which is how the
 * suggestion menu gets anchored under the caret's line without the classic
 * throwaway-mirror-div trick: the mirror is already in the DOM.
 */
function rangeAtCharOffset(root: Element, offset: number): Range | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let remaining = offset;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0;
    if (remaining < length) {
      const range = document.createRange();
      range.setStart(node, remaining);
      range.setEnd(node, remaining + 1);
      return range;
    }
    remaining -= length;
  }
  return null;
}

/**
 * Bluesky-style suggestions while typing `@` or `#` in a composer.
 *
 * Give it the draft, its setter, and the textarea ref from `useEmojiField`;
 * spread nothing — render the returned `menu` inside `.composer-input` (which
 * is `position: relative`) and pass `onKeyDown` to the textarea. While the
 * menu is open it owns ArrowUp/Down (move), Enter/Tab (accept) and Escape
 * (dismiss — stopped from propagating so a dialog composer doesn't close).
 *
 * Suggestions are decoration: every fetch is debounced, last-request-wins,
 * and a failed lookup simply shows nothing rather than surfacing an error.
 */
export function useComposerTypeahead({
  text,
  onTextChange,
  maxLength,
  fieldRef,
}: {
  text: string;
  onTextChange: (next: string) => void;
  maxLength: number;
  fieldRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const [entity, setEntity] = useState<TypeaheadEntity | null>(null);
  const [users, setUsers] = useState<UserDiscovery[]>([]);
  const [tags, setTags] = useState<TrendingHashtag[]>([]);
  const [index, setIndex] = useState(0);
  // `type:start` of an entity Escape closed: stays hidden until the caret
  // leaves it, so the menu doesn't pop right back on the next keystroke.
  const [dismissed, setDismissed] = useState<string | null>(null);
  // Viewport coordinates: the menu renders in a portal with position:fixed so
  // a dialog composer's overflow clipping can never cut it off. Exactly one of
  // top/bottom is set — bottom means "no room below the caret, open upward".
  const [anchor, setAnchor] = useState<{
    top?: number;
    bottom?: number;
    left: number;
    width: number;
    maxHeight: number;
  } | null>(null);
  const requestRef = useRef(0);

  const syncEntity = useCallback(() => {
    const el = fieldRef.current;
    if (!el || document.activeElement !== el) {
      setEntity(null);
      return;
    }
    const caret = el.selectionStart ?? el.value.length;
    const next = findTypeaheadEntity(el.value, caret);
    setEntity((prev) =>
      prev &&
      next &&
      prev.type === next.type &&
      prev.start === next.start &&
      prev.end === next.end &&
      prev.query === next.query
        ? prev
        : next,
    );
  }, [fieldRef]);

  useEffect(syncEntity, [text, syncEntity]);

  // Caret moves that don't change the text (arrows, clicks) still fire
  // selectionchange on the document; blur must close the menu outright.
  useEffect(() => {
    const el = fieldRef.current;
    const close = () => setEntity(null);
    document.addEventListener("selectionchange", syncEntity);
    el?.addEventListener("blur", close);
    return () => {
      document.removeEventListener("selectionchange", syncEntity);
      el?.removeEventListener("blur", close);
    };
  }, [fieldRef, syncEntity]);

  useEffect(() => {
    if (!entity) {
      setDismissed(null);
    }
  }, [entity]);

  const entityType = entity?.type ?? null;
  const entityQuery = entity?.query ?? null;
  useEffect(() => {
    if (entityType === null || entityQuery === null) {
      setUsers([]);
      setTags([]);
      return;
    }
    const requestId = ++requestRef.current;
    const timer = window.setTimeout(async () => {
      try {
        if (entityType === "mention") {
          const rows = await listUsers(entityQuery);
          if (requestRef.current === requestId) {
            setUsers(rows);
            setIndex(0);
          }
        } else {
          const rows = await suggestHashtags(entityQuery);
          if (requestRef.current === requestId) {
            setTags(rows);
            setIndex(0);
          }
        }
      } catch {
        if (requestRef.current === requestId) {
          setUsers([]);
          setTags([]);
        }
      }
    }, 150);
    return () => window.clearTimeout(timer);
  }, [entityType, entityQuery]);

  // Anchor the menu under the trigger character's line, measured off the
  // highlight backdrop.
  const measure = useCallback(() => {
    if (!entity) {
      setAnchor(null);
      return;
    }
    const el = fieldRef.current;
    const container = el?.parentElement;
    const mirror = container?.querySelector(".composer-highlight");
    if (!el || !container || !mirror) {
      setAnchor(null);
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const charRect = rangeAtCharOffset(mirror, entity.start)?.getBoundingClientRect();
    const line =
      charRect && (charRect.width > 0 || charRect.height > 0)
        ? charRect
        : el.getBoundingClientRect();

    const width = Math.min(300, Math.max(200, containerRect.width));
    // Align with the trigger, kept inside the composer then inside the viewport.
    const left = Math.max(
      8,
      Math.min(
        Math.max(containerRect.left, line.left),
        containerRect.right - width,
        window.innerWidth - width - 8,
      ),
    );

    // Open downward when the space under the caret's line can hold a useful
    // list; otherwise open upward, pinned above the line. maxHeight tracks the
    // chosen side so the menu scrolls internally instead of leaving the screen.
    const below = window.innerHeight - line.bottom - 12;
    const above = line.top - 12;
    if (below >= 220 || below >= above) {
      setAnchor({ top: line.bottom + 4, left, width, maxHeight: Math.min(320, below) });
    } else {
      setAnchor({
        bottom: window.innerHeight - line.top + 4,
        left,
        width,
        maxHeight: Math.min(320, above),
      });
    }
  }, [entity, fieldRef]);

  // Layout effect: the backdrop must have re-rendered this draft before it is
  // measured. Scroll and resize move the fixed-position anchor, so re-measure
  // on both (capture phase catches scrolls of any ancestor, not just window).
  useLayoutEffect(measure, [measure, text]);
  useEffect(() => {
    if (!entity) {
      return;
    }
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [entity, measure]);

  const count = entity?.type === "mention" ? users.length : tags.length;
  const open =
    entity !== null && count > 0 && dismissed !== `${entity.type}:${entity.start}`;

  const accept = useCallback(
    (body: string) => {
      if (!entity) {
        return;
      }
      const insert = (entity.type === "mention" ? "@" : "#") + body;
      const rest = text.slice(entity.end);
      // A trailing space ends the entity and keeps typing flowing, unless one
      // is already there — or the draft has no room left for it.
      const spaceFollows = rest.startsWith(" ");
      let replacement = spaceFollows ? insert : `${insert} `;
      let next = text.slice(0, entity.start) + replacement + rest;
      if (next.length > maxLength && replacement.endsWith(" ")) {
        replacement = insert;
        next = text.slice(0, entity.start) + replacement + rest;
      }
      if (next.length > maxLength) {
        return;
      }
      // Land after the space that now follows the entity, whichever branch
      // provided it; in the no-room fallback there is none, so stay put.
      const caret =
        entity.start + replacement.length + (spaceFollows ? 1 : 0);
      onTextChange(next);
      setEntity(null);
      const el = fieldRef.current;
      requestAnimationFrame(() => {
        if (el) {
          el.focus();
          const position = Math.min(caret, next.length);
          el.setSelectionRange(position, position);
        }
      });
    },
    [entity, text, maxLength, onTextChange, fieldRef],
  );

  const acceptCurrent = useCallback(() => {
    if (!entity) {
      return;
    }
    if (entity.type === "mention") {
      const user = users[index];
      if (user) {
        accept(user.username);
      }
    } else {
      const tag = tags[index];
      if (tag) {
        accept(tag.tag);
      }
    }
  }, [entity, users, tags, index, accept]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!open || !entity) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((current) => (current + 1) % count);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((current) => (current - 1 + count) % count);
      } else if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        acceptCurrent();
      } else if (event.key === "Escape") {
        event.preventDefault();
        // The dialog composers close on window-level Escape; this one is ours.
        event.stopPropagation();
        setDismissed(`${entity.type}:${entity.start}`);
      }
    },
    [open, entity, count, acceptCurrent],
  );

  const menu =
    open && entity && anchor
      ? createPortal(
          <div
            className="typeahead-menu"
            style={{
              top: anchor.top,
              bottom: anchor.bottom,
              left: anchor.left,
              width: anchor.width,
              maxHeight: anchor.maxHeight,
            }}
            role="listbox"
            aria-label={
              entity.type === "mention" ? "Mention suggestions" : "Hashtag suggestions"
            }
          >
        {entity.type === "mention"
          ? users.map((user, itemIndex) => (
              <button
                key={user.id}
                type="button"
                role="option"
                aria-selected={itemIndex === index}
                className={
                  itemIndex === index ? "typeahead-item selected" : "typeahead-item"
                }
                // Keep focus (and the caret) in the textarea through the click.
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setIndex(itemIndex)}
                onClick={() => accept(user.username)}
              >
                <Avatar user={user} size="small" />
                <span className="typeahead-labels">
                  <span className="typeahead-primary">{displayName(user)}</span>
                  <span className="typeahead-secondary">@{user.username}</span>
                </span>
              </button>
            ))
          : tags.map((tag, itemIndex) => (
              <button
                key={tag.tag}
                type="button"
                role="option"
                aria-selected={itemIndex === index}
                className={
                  itemIndex === index ? "typeahead-item selected" : "typeahead-item"
                }
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setIndex(itemIndex)}
                onClick={() => accept(tag.tag)}
              >
                <span className="typeahead-labels">
                  <span className="typeahead-primary">#{tag.tag}</span>
                  <span className="typeahead-secondary">
                    {tag.post_count} {tag.post_count === 1 ? "post" : "posts"}
                  </span>
                </span>
              </button>
            ))}
          </div>,
          document.body,
        )
      : null;

  return { menu, onKeyDown };
}

function InlineImage({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);
  const [viewing, setViewing] = useState(false);

  if (failed) {
    return (
      <a
        className="tweet-link"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(event) => event.stopPropagation()}
      >
        {url}
      </a>
    );
  }
  return (
    <>
      <button
        type="button"
        className="tweet-media-link"
        onClick={(event) => {
          event.stopPropagation();
          setViewing(true);
        }}
      >
        <img
          className="tweet-media-image"
          src={url}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
        />
      </button>
      {viewing ? (
        <ImageLightbox
          images={[{ src: url, alt: "" }]}
          initialIndex={0}
          onClose={() => setViewing(false)}
        />
      ) : null}
    </>
  );
}

// Renders user content as text, turning URLs into links, image URLs into inline
// images, and #hashtags / @mentions into in-app links (to search / the profile).
// Only http(s) URLs are matched, so no javascript: injection.
export function RichContent({ text }: { text: string }) {
  const tokens = useMemo(() => tokenizeContent(text), [text]);

  return (
    <>
      {tokens.map((token, index) => {
        if (token.type === "text") {
          return token.value;
        }
        if (token.type === "hashtag") {
          return (
            <Link
              key={index}
              className="tweet-entity"
              to={`/hashtag/${encodeURIComponent(token.value.slice(1))}`}
              onClick={(event) => event.stopPropagation()}
            >
              {token.value}
            </Link>
          );
        }
        if (token.type === "mention") {
          return (
            <Link
              key={index}
              className="tweet-entity"
              to={`/${encodeURIComponent(token.value.slice(1))}`}
              onClick={(event) => event.stopPropagation()}
            >
              {token.value}
            </Link>
          );
        }
        if (isImageUrl(token.value)) {
          return <InlineImage key={index} url={token.value} />;
        }
        return (
          <a
            key={index}
            className="tweet-link"
            href={token.value}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            {token.value}
          </a>
        );
      })}
    </>
  );
}

// A native <video> player limited to the controls we want: play/pause,
// timeline + duration, volume, and fullscreen. Picture-in-picture and the
// settings/download menu are intentionally suppressed. Before it is played it
// shows the first frame (preload="metadata") with the browser's play overlay.
function VideoPlayer({ src, className }: { src: string; className: string }) {
  return (
    <video
      className={className}
      src={src}
      controls
      playsInline
      preload="metadata"
      disablePictureInPicture
      controlsList="nodownload noplaybackrate noremoteplayback"
      onClick={(event) => event.stopPropagation()}
    />
  );
}

/** One image in the fullscreen viewer: its resolved URL and alt text ("" = none). */
export type LightboxImage = { src: string; alt: string };

/** The image's file name from the URL path — used to name downloads. */
function imageFileName(src: string): string {
  try {
    const path = new URL(src, window.location.href).pathname;
    return decodeURIComponent(path.split("/").pop() ?? "");
  } catch {
    return "";
  }
}

// Bluesky-style fullscreen image viewer: dark cover with share/download
// top-left, close top-right and the image's alt text bottom-left. Multi-image
// posts get a dot indicator top-center plus prev/next arrows at the sides;
// the arrow keys navigate and Escape (or a backdrop click) closes.
export function ImageLightbox({
  images,
  initialIndex,
  onClose,
}: {
  images: LightboxImage[];
  initialIndex: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [menuOpen, setMenuOpen] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  const [captionExpanded, setCaptionExpanded] = useState(false);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const { src, alt } = images[index];
  const fileName = imageFileName(src);

  useEffect(() => {
    function handleKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowLeft") {
        setIndex((current) => Math.max(0, current - 1));
      } else if (event.key === "ArrowRight") {
        setIndex((current) => Math.min(images.length - 1, current + 1));
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [images.length, onClose]);

  // The page must not scroll behind the fullscreen cover.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    setMenuOpen(false);
    setCaptionExpanded(false);
  }, [index]);

  // "Link copied" confirmation fades on its own (the menu is gone by then).
  useEffect(() => {
    if (!linkCopied) {
      return;
    }
    const timer = setTimeout(() => setLinkCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [linkCopied]);

  async function shareImage() {
    const url = new URL(src, window.location.href).href;
    try {
      if (navigator.share) {
        await navigator.share({ url });
      } else {
        await navigator.clipboard.writeText(url);
        setLinkCopied(true);
      }
    } catch {
      // Dismissed share sheet / clipboard denied — nothing to report.
    }
  }

  // Swipe left/right anywhere on the cover to change images. Only a clearly
  // horizontal single-finger swipe counts, so pinch-zoom and sloppy taps
  // (which still close via the backdrop click) are left alone.
  function handleTouchStart(event: TouchEvent<HTMLDivElement>) {
    touchStart.current =
      event.touches.length === 1
        ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
        : null;
  }

  function handleTouchEnd(event: TouchEvent<HTMLDivElement>) {
    const start = touchStart.current;
    touchStart.current = null;
    if (!start || images.length < 2) {
      return;
    }
    const deltaX = event.changedTouches[0].clientX - start.x;
    const deltaY = event.changedTouches[0].clientY - start.y;
    if (Math.abs(deltaX) < 48 || Math.abs(deltaX) < Math.abs(deltaY) * 1.5) {
      return;
    }
    if (deltaX < 0) {
      setIndex((current) => Math.min(images.length - 1, current + 1));
    } else {
      setIndex((current) => Math.max(0, current - 1));
    }
  }

  async function downloadImage() {
    try {
      const response = await fetch(src);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = fileName || "image";
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch {
      // Cross-origin images we can't fetch still open in a tab to save from.
      window.open(src, "_blank", "noopener");
    }
  }

  // Portaled to <body>: rendered in place it sits inside the clickable tweet
  // card's DOM, and mobile browsers compute the tap-highlight from the DOM
  // tree — every tap in the viewer flashed the whole card blue and ran the
  // card's capture-phase handlers. stopPropagation only contains the React
  // tree; moving the DOM subtree out is what actually detaches the card.
  return createPortal(
    <div
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="Image viewer"
      onClick={(event) => {
        event.stopPropagation();
        if (menuOpen) {
          setMenuOpen(false);
          return;
        }
        onClose();
      }}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <div className="lightbox-actions" onClick={(event) => event.stopPropagation()}>
        <button
          type="button"
          className="lightbox-button"
          onClick={() => setMenuOpen((value) => !value)}
          aria-label="Image options"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <MoreVertical size={20} aria-hidden="true" />
        </button>
        {menuOpen ? (
          <div className="post-menu-dropdown lightbox-dropdown" role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                void shareImage();
              }}
            >
              <Share2 size={16} aria-hidden="true" />
              <span>Share image</span>
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                void downloadImage();
              }}
            >
              <Download size={16} aria-hidden="true" />
              <span>Download image</span>
            </button>
          </div>
        ) : null}
      </div>
      {linkCopied ? <div className="lightbox-toast">Link copied</div> : null}
      <button
        type="button"
        className="lightbox-button lightbox-close"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        aria-label="Close image viewer"
      >
        <X size={20} aria-hidden="true" />
      </button>
      {images.length > 1 ? (
        <div className="lightbox-dots" aria-label={`Image ${index + 1} of ${images.length}`}>
          {images.map((_, dot) => (
            <span key={dot} className={dot === index ? "active" : undefined} />
          ))}
        </div>
      ) : null}
      <img
        className="lightbox-image"
        src={src}
        alt={alt}
        onClick={(event) => event.stopPropagation()}
      />
      {index > 0 ? (
        <button
          type="button"
          className="lightbox-button lightbox-nav lightbox-prev"
          onClick={(event) => {
            event.stopPropagation();
            setIndex(index - 1);
          }}
          aria-label="Previous image"
        >
          <ChevronLeft size={20} aria-hidden="true" />
        </button>
      ) : null}
      {index < images.length - 1 ? (
        <button
          type="button"
          className="lightbox-button lightbox-nav lightbox-next"
          onClick={(event) => {
            event.stopPropagation();
            setIndex(index + 1);
          }}
          aria-label="Next image"
        >
          <ChevronRight size={20} aria-hidden="true" />
        </button>
      ) : null}
      {alt ? (
        // Clicking the caption never closes the viewer (so the text can be
        // selected and copied); it toggles the two-line clamp for long alts.
        <div
          className={captionExpanded ? "lightbox-caption expanded" : "lightbox-caption"}
          onClick={(event) => {
            event.stopPropagation();
            if (window.getSelection()?.toString()) {
              return; // The click ended a text selection — leave it alone.
            }
            setCaptionExpanded((value) => !value);
          }}
        >
          {alt}
        </div>
      ) : null}
    </div>,
    document.body,
  );
}

// Renders a tweet/comment's uploaded media (media_urls), resolved to absolute
// URLs, with per-image alt text (media_alts). Images open in the fullscreen
// lightbox; videos play inline. Lays out as a 2-column grid when there is
// more than one item.
export function MediaGallery({ urls, alts = [] }: { urls: string[]; alts?: string[] }) {
  const items = urls
    .map((url, i) => ({ src: resolveMediaUrl(url), alt: alts[i] ?? "" }))
    .filter((item): item is LightboxImage => Boolean(item.src));
  const images = items.filter((item) => !isVideoUrl(item.src));
  const [viewing, setViewing] = useState<number | null>(null);

  if (items.length === 0) {
    return null;
  }

  const viewer =
    viewing != null ? (
      <ImageLightbox
        images={images}
        initialIndex={viewing}
        onClose={() => setViewing(null)}
      />
    ) : null;

  if (items.length === 1) {
    const item = items[0];
    return (
      <div className="tweet-media-gallery">
        {isVideoUrl(item.src) ? (
          <VideoPlayer src={item.src} className="tweet-media-video" />
        ) : (
          <button
            type="button"
            className="tweet-media-link"
            onClick={(event) => {
              event.stopPropagation();
              setViewing(0);
            }}
          >
            <img className="tweet-media-image" src={item.src} alt={item.alt} loading="lazy" />
          </button>
        )}
        {viewer}
      </div>
    );
  }

  return (
    <>
      <div className="media-grid" data-count={items.length}>
        {items.map((item) =>
          isVideoUrl(item.src) ? (
            <div key={item.src} className="media-grid__cell">
              <VideoPlayer src={item.src} className="media-grid__video" />
            </div>
          ) : (
            <button
              key={item.src}
              type="button"
              className="media-grid__cell"
              onClick={(event) => {
                event.stopPropagation();
                setViewing(images.indexOf(item));
              }}
            >
              <img src={item.src} alt={item.alt} loading="lazy" />
            </button>
          ),
        )}
      </div>
      {viewer}
    </>
  );
}

// Composer control: the "add image" button plus its hidden multi-file input.
export function MediaButton({ attachment }: { attachment: MediaAttachment }) {
  return (
    <>
      <input
        ref={attachment.inputRef}
        type="file"
        accept={ACCEPTED_MEDIA}
        multiple
        onChange={attachment.onFileChange}
        hidden
      />
      <button
        type="button"
        className="icon-button media-trigger"
        onClick={(event) => {
          event.stopPropagation();
          attachment.openPicker();
        }}
        disabled={attachment.atLimit}
        aria-label="Add photo or video"
        title={attachment.atLimit ? "Media limit reached" : "Add photo or video"}
      >
        <ImageIcon size={20} aria-hidden="true" />
      </button>
    </>
  );
}

// Composer preview: a thumbnail grid of the attached images (each removable),
// a spinner while uploads are in flight, plus any upload error.
// Bluesky-style "Add alt text" dialog for one attached image: a preview of
// the picture above a description field with a live character budget.
function AltTextModal({
  src,
  initialAlt,
  onSave,
  onClose,
}: {
  src: string;
  initialAlt: string;
  onSave: (alt: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialAlt);

  function save() {
    onSave(value.trim());
    onClose();
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        event.stopPropagation();
        onClose();
      }}
    >
      <section
        className="modal alt-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Add alt text"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="alt-modal-head">
          <h2>Add alt text</h2>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close alt text editor"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="alt-modal-image">
          <img src={src} alt="" />
        </div>
        <label className="alt-modal-label" htmlFor="alt-text-input">
          Descriptive alt text
        </label>
        <textarea
          id="alt-text-input"
          className="alt-modal-input"
          placeholder="Alt text"
          maxLength={MAX_ALT_LENGTH}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
        />
        <div className="alt-modal-actions">
          <span className="alt-modal-count" aria-hidden="true">
            {MAX_ALT_LENGTH - value.length}
          </span>
          <button type="button" className="primary-button alt-modal-save" onClick={save}>
            Save
          </button>
        </div>
      </section>
    </div>
  );
}

export function MediaPreview({ attachment }: { attachment: MediaAttachment }) {
  const [altEditing, setAltEditing] = useState<MediaItem | null>(null);

  if (attachment.items.length === 0 && !attachment.uploading && !attachment.error) {
    return null;
  }

  const count = attachment.items.length;

  const removeButton = (item: MediaItem) => (
    <button
      type="button"
      className="composer-media-remove"
      onClick={(event) => {
        event.stopPropagation();
        attachment.remove(item.url);
      }}
      aria-label="Remove media"
    >
      <X size={16} aria-hidden="true" />
    </button>
  );

  // Images only: <video> carries no alt attribute.
  const altButton = (item: MediaItem) =>
    isVideoUrl(item.url) ? null : (
      <button
        type="button"
        className="composer-media-alt"
        onClick={(event) => {
          event.stopPropagation();
          setAltEditing(item);
        }}
        aria-label={item.alt ? "Edit alt text" : "Add alt text"}
      >
        {item.alt ? (
          <>
            <Check size={14} aria-hidden="true" />
            <span>ALT</span>
          </>
        ) : (
          "+ ALT"
        )}
      </button>
    );

  return (
    <div className="composer-media">
      {count === 1 ? (
        <div className="composer-media-item">
          {resolveMediaUrl(attachment.items[0].url) ? (
            isVideoUrl(attachment.items[0].url) ? (
              <VideoPlayer
                src={resolveMediaUrl(attachment.items[0].url)!}
                className="composer-media-image"
              />
            ) : (
              <img
                className="composer-media-image"
                src={resolveMediaUrl(attachment.items[0].url)!}
                alt={attachment.items[0].alt || "Attached preview"}
              />
            )
          ) : null}
          {altButton(attachment.items[0])}
          {removeButton(attachment.items[0])}
        </div>
      ) : count > 1 ? (
        <div className="media-grid" data-count={count}>
          {attachment.items.map((item) => {
            const src = resolveMediaUrl(item.url);
            return (
              <div className="media-grid__cell" key={item.url}>
                {src ? (
                  isVideoUrl(item.url) ? (
                    <VideoPlayer src={src} className="media-grid__video" />
                  ) : (
                    <img src={src} alt={item.alt || "Attached preview"} />
                  )
                ) : null}
                {altButton(item)}
                {removeButton(item)}
              </div>
            );
          })}
        </div>
      ) : null}
      {attachment.uploading ? (
        <div className="composer-media-loading">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Uploading…</span>
        </div>
      ) : null}
      {attachment.error ? <p className="form-error">{attachment.error}</p> : null}
      {altEditing ? (
        <AltTextModal
          src={resolveMediaUrl(altEditing.url) ?? altEditing.url}
          initialAlt={altEditing.alt}
          onSave={(alt) => attachment.setAlt(altEditing.url, alt)}
          onClose={() => setAltEditing(null)}
        />
      ) : null}
    </div>
  );
}

// A "⋯" dropdown with Edit/Delete, shown only on the author's own posts.
// The "···" menu on a post. For your own posts it offers Edit/Delete; for
// anyone else's it offers moderation of the author (Mute/Block). A post is
// never both, so the two sets are mutually exclusive in practice.
export function PostMenu({
  onEdit,
  onDelete,
  authorUsername,
  onMute,
  onBlock,
  onReport,
}: {
  onEdit?: () => void;
  onDelete?: () => void;
  authorUsername?: string;
  onMute?: () => void;
  onBlock?: () => void;
  onReport?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onDocMouseDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  function choose(action: () => void) {
    setOpen(false);
    action();
  }

  return (
    <div className="post-menu" ref={ref}>
      <button
        type="button"
        className="icon-button post-menu-trigger"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        aria-label="More options"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
      {open ? (
        <div className="post-menu-dropdown" role="menu" onClick={(event) => event.stopPropagation()}>
          {onEdit ? (
            <button type="button" role="menuitem" onClick={() => choose(onEdit)}>
              <Pencil size={16} aria-hidden="true" />
              <span>Edit</span>
            </button>
          ) : null}
          {onDelete ? (
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => choose(onDelete)}
            >
              <Trash2 size={16} aria-hidden="true" />
              <span>Delete</span>
            </button>
          ) : null}
          {onMute ? (
            <button type="button" role="menuitem" onClick={() => choose(onMute)}>
              <VolumeX size={16} aria-hidden="true" />
              <span>Mute{authorUsername ? ` @${authorUsername}` : ""}</span>
            </button>
          ) : null}
          {onBlock ? (
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => choose(onBlock)}
            >
              <Ban size={16} aria-hidden="true" />
              <span>Block{authorUsername ? ` @${authorUsername}` : ""}</span>
            </button>
          ) : null}
          {onReport ? (
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => choose(onReport)}
            >
              <Flag size={16} aria-hidden="true" />
              <span>Report post</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// The reasons offered when reporting a post; the values match the backend's
// ReportReason literal.
const REPORT_REASONS: { value: ReportReason; label: string }[] = [
  { value: "spam", label: "It's spam" },
  { value: "abuse", label: "Abuse or harassment" },
  { value: "hate", label: "Hateful speech or symbols" },
  { value: "violence", label: "Violent speech or threats" },
  { value: "sensitive", label: "Sensitive or disturbing media" },
  { value: "misinformation", label: "Misleading information" },
  { value: "other", label: "Something else" },
];

// Modal for reporting a post or an account: pick a reason, add optional
// detail, submit. On success it swaps to a confirmation -- a report is a
// moderation signal, so it deliberately does not hide anything from the
// reporter afterwards.
export function ReportModal({
  postId,
  userId,
  username,
  onClose,
}: {
  /** Exactly one of `postId` / `userId` — the report's target. */
  postId?: number;
  userId?: number;
  username: string;
  onClose: () => void;
}) {
  const reportingUser = userId != null;
  const [reason, setReason] = useState<ReportReason | null>(null);
  const [details, setDetails] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit() {
    if (!reason) {
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      if (userId != null) {
        await reportUser(userId, reason, details);
      } else {
        await reportPost(postId!, reason, details);
      }
      setDone(true);
    } catch (err) {
      setError(getErrorMessage(err));
      setSubmitting(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        event.stopPropagation();
        onClose();
      }}
    >
      <section
        className="modal report-modal"
        role="dialog"
        aria-modal="true"
        aria-label={
          reportingUser ? `Report @${username}` : `Report @${username}'s post`
        }
        onClick={(event) => event.stopPropagation()}
      >
        {done ? (
          <>
            <h2>Thanks for reporting</h2>
            <p>
              We use reports like yours to understand what&apos;s happening and
              keep the community safe. You won&apos;t be notified of the outcome.
            </p>
            <div className="confirm-actions">
              <button className="primary-button" onClick={onClose}>
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <h2>{reportingUser ? `Report @${username}` : "Report post"}</h2>
            <p className="report-modal-intro">
              {reportingUser
                ? `Why are you reporting @${username}?`
                : `Why are you reporting @${username}'s post?`}
            </p>
            <fieldset className="report-reasons">
              {REPORT_REASONS.map((option) => (
                <label className="report-reason" key={option.value}>
                  <input
                    type="radio"
                    name="report-reason"
                    value={option.value}
                    checked={reason === option.value}
                    onChange={() => setReason(option.value)}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </fieldset>
            <textarea
              className="report-details"
              placeholder="Add anything that would help a reviewer (optional)"
              maxLength={280}
              value={details}
              onChange={(event) => setDetails(event.target.value)}
            />
            {error ? <p className="form-error">{error}</p> : null}
            <div className="confirm-actions">
              <button
                className="outline-button"
                onClick={onClose}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                className="danger-button"
                onClick={() => void submit()}
                disabled={!reason || submitting}
              >
                {submitting ? "Reporting…" : "Report"}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

// Modal confirmation used before a destructive delete.
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  busyLabel = "Working…",
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  busyLabel?: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        event.stopPropagation();
        onCancel();
      }}
    >
      <section
        className="modal confirm-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <h2>{title}</h2>
        <p>{message}</p>
        <div className="confirm-actions">
          <button className="outline-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="danger-button" onClick={onConfirm} disabled={busy}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

// Editing a post opens this compose-style dialog (same card, avatar column,
// tools and audience row as the compose modal) so editing feels the same as
// posting. Media is seeded from the post and can be added to or removed
// before saving; Escape or the backdrop cancels.
export function PostEditor({
  initialContent,
  initialMedia = [],
  initialAlts = [],
  maxLength,
  saving,
  onSave,
  onCancel,
  visibility,
  onVisibilityChange,
}: {
  initialContent: string;
  initialMedia?: string[];
  initialAlts?: string[];
  maxLength: number;
  saving: boolean;
  onSave: (content: string, mediaUrls: string[], mediaAlts: string[]) => void;
  onCancel: () => void;
  // When both are given (tweet edit), the editor shows an audience selector.
  // Omitted for comments, which have no audience of their own.
  visibility?: TweetVisibility;
  onVisibilityChange?: (value: TweetVisibility) => void;
}) {
  const currentUser = useCurrentUser();
  const [value, setValue] = useState(initialContent);
  const media = useMediaAttachment(initialMedia, initialAlts);
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    value,
    setValue,
    maxLength,
  );
  const typeahead = useComposerTypeahead({
    text: value,
    onTextChange: setValue,
    maxLength,
    fieldRef: fieldProps.ref,
  });
  const remaining = maxLength - value.length;
  const canSave =
    (value.trim().length > 0 || media.mediaUrls.length > 0) && !saving && !media.uploading;

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        event.stopPropagation();
        onCancel();
      }}
    >
      <div
        className="compose-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Edit post"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="compose-modal-head">
          <button
            type="button"
            className="icon-button"
            onClick={onCancel}
            aria-label="Close editor"
            disabled={saving}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave) {
              onSave(value.trim(), media.mediaUrls, media.mediaAlts);
            }
          }}
        >
          {currentUser ? <Avatar user={currentUser} /> : <span aria-hidden="true" />}
          <div className="composer-body">
            <div className="composer-input">
              <ComposerHighlight text={value} />
              <textarea
                {...fieldProps}
                onKeyDown={typeahead.onKeyDown}
                value={value}
                rows={1}
                maxLength={maxLength}
                aria-label="Edit content"
                autoFocus
              />
              {typeahead.menu}
            </div>
            <MediaPreview attachment={media} />
          </div>
          {visibility != null && onVisibilityChange ? (
            <div className="composer-visibility">
              <VisibilityPicker
                value={visibility}
                onChange={onVisibilityChange}
                disabled={saving}
              />
            </div>
          ) : null}
          <div className="composer-actions">
            <div className="composer-tools">
              <EmojiPicker onSelect={insertEmoji} />
              <MediaButton attachment={media} />
            </div>
            <span className={remaining < 30 ? "counter warn" : "counter"}>{remaining}</span>
            <button className="primary-button compact" disabled={!canSave}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * The original post embedded inside a quote tweet, shown as a bordered card.
 *
 * In `preview` mode (inside the quote composer) it is inert; otherwise clicking
 * it opens the quoted post.
 */
export function QuotedPostCard({
  post,
  preview = false,
}: {
  post: QuotedPost;
  preview?: boolean;
}) {
  const navigate = useNavigate();
  return (
    <div
      className="quoted-post"
      role={preview ? undefined : "link"}
      tabIndex={preview ? undefined : 0}
      onClick={
        preview
          ? undefined
          : (event) => {
              event.stopPropagation();
              navigate(`/tweet/${post.id}`);
            }
      }
      onKeyDown={
        preview
          ? undefined
          : (event) => {
              if (event.target !== event.currentTarget) return;
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                navigate(`/tweet/${post.id}`);
              }
            }
      }
    >
      <header className="quoted-post-head">
        <Avatar user={post.author} size="small" />
        <strong>{displayName(post.author)}</strong>
        <span>@{post.author.username}</span>
      </header>
      {post.content ? (
        <div className="quoted-post-content">
          <RichContent text={post.content} />
        </div>
      ) : null}
      {post.media_urls.length > 0 ? (
        <MediaGallery urls={post.media_urls} alts={post.media_alts} />
      ) : null}
    </div>
  );
}

/**
 * Twitter-style quote composer: an optional comment plus the embedded original.
 * Submitting creates a new top-level post that quotes `quoted`.
 */
export function QuoteComposer({
  quoted,
  onClose,
  onQuoted,
}: {
  quoted: QuotedPost;
  onClose: () => void;
  onQuoted?: (tweet: Tweet) => void;
}) {
  const currentUser = useCurrentUser();
  const [content, setContent] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    content,
    setContent,
    280,
  );
  const typeahead = useComposerTypeahead({
    text: content,
    onTextChange: setContent,
    maxLength: 280,
    fieldRef: fieldProps.ref,
  });
  const media = useMediaAttachment();
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const remaining = 280 - content.length;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (posting || remaining < 0 || media.uploading) {
      return;
    }
    setPosting(true);
    setError("");
    try {
      const tweet = await createTweet(
        content.trim(),
        media.mediaUrls,
        media.mediaAlts,
        quoted.id,
      );
      onQuoted?.(tweet);
      onClose();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setPosting(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        // The modal renders inside a clickable tweet card; without this a
        // backdrop click would also navigate to that tweet.
        event.stopPropagation();
        onClose();
      }}
    >
      <div
        className="quote-composer"
        role="dialog"
        aria-modal="true"
        aria-label="Quote post"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="quote-composer-head">
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <form className="quote-composer-form" onSubmit={handleSubmit}>
          {/* Same shape as the compose dialog: the author's avatar on the
              left, and everything they are writing — comment, attached media,
              the quoted card — stacked in the text column beside it. */}
          <div className="quote-input-row">
            {currentUser ? <Avatar user={currentUser} /> : null}
            <div className="quote-input-body">
              <div className="composer-input">
                <ComposerHighlight text={content} />
                <textarea
                  {...fieldProps}
                  onKeyDown={typeahead.onKeyDown}
                  value={content}
                  rows={1}
                  maxLength={280}
                  placeholder="Add a comment"
                  aria-label="Quote comment"
                  autoFocus
                />
                {typeahead.menu}
              </div>
              <MediaPreview attachment={media} />
              <QuotedPostCard post={quoted} preview />
            </div>
          </div>
          {error ? <p className="form-error">{error}</p> : null}
          <div className="composer-actions">
            <div className="composer-tools">
              <EmojiPicker onSelect={insertEmoji} />
              <MediaButton attachment={media} />
            </div>
            <span className={remaining < 30 ? "counter warn" : "counter"}>{remaining}</span>
            <button
              className="primary-button compact"
              disabled={posting || remaining < 0 || media.uploading}
            >
              {posting ? "Posting…" : "Post"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/** The generic Open Graph "unfurl" card (presentational — data comes from PostBody). */
/** The subset of a Tweet/Comment the reply composer needs to show its target. */
type ReplyTarget = {
  id: number;
  content: string;
  media_urls: string[];
  media_alts: string[];
  created_at: string;
  author: UserSummary;
};

/**
 * Twitter-style reply composer: the post being replied to sits above a thread
 * connector, then "Replying to @handle", then the composer itself.
 *
 * `onSubmit` decides which endpoint runs — replying to a tweet creates a
 * comment, replying to a comment creates a nested reply — so this component
 * stays the same for both.
 */
export function ReplyComposer({
  target,
  onSubmit,
  onClose,
}: {
  target: ReplyTarget;
  onSubmit: (content: string, mediaUrls: string[], mediaAlts: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const currentUser = useCurrentUser();
  const [content, setContent] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    content,
    setContent,
    1000,
  );
  const typeahead = useComposerTypeahead({
    text: content,
    onTextChange: setContent,
    maxLength: 1000,
    fieldRef: fieldProps.ref,
  });
  const media = useMediaAttachment();
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");

  const empty = !content.trim() && media.mediaUrls.length === 0;

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (posting || empty || media.uploading) {
      return;
    }
    setPosting(true);
    setError("");
    try {
      await onSubmit(content.trim(), media.mediaUrls, media.mediaAlts);
      onClose();
    } catch (err) {
      setError(getErrorMessage(err));
      setPosting(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        // The modal renders inside a clickable tweet card; without this a
        // backdrop click would also navigate to that tweet.
        event.stopPropagation();
        onClose();
      }}
    >
      <div
        className="reply-composer"
        role="dialog"
        aria-modal="true"
        aria-label={`Reply to @${target.author.username}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="reply-composer-head">
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>

        <form className="reply-composer-form" onSubmit={handleSubmit}>
          {/* The post being replied to, with a thread line running into the composer. */}
          <div className="reply-target">
            <div className="reply-target-rail">
              <Avatar user={target.author} />
              <span className="reply-thread-line" aria-hidden="true" />
            </div>
            <div className="reply-target-body">
              <header className="reply-target-head">
                <strong>{displayName(target.author)}</strong>
                <span className="reply-target-handle">@{target.author.username}</span>
                <span className="reply-target-dot" aria-hidden="true">
                  ·
                </span>
                <time dateTime={target.created_at}>
                  {formatCompactDate(target.created_at)}
                </time>
              </header>
              {target.content ? (
                <p className="reply-target-content">
                  <RichContent text={target.content} />
                </p>
              ) : null}
              {target.media_urls.length > 0 ? (
                <MediaGallery urls={target.media_urls} alts={target.media_alts} />
              ) : null}
              <p className="reply-target-replying">
                Replying to <span className="reply-mention">@{target.author.username}</span>
              </p>
            </div>
          </div>

          <div className="reply-input-row">
            {currentUser ? <Avatar user={currentUser} /> : null}
            <div className="reply-input-body">
              <div className="composer-input">
                <ComposerHighlight text={content} />
                <textarea
                  {...fieldProps}
                  onKeyDown={typeahead.onKeyDown}
                  value={content}
                  rows={1}
                  maxLength={1000}
                  placeholder="Post your reply"
                  aria-label="Post your reply"
                  autoFocus
                />
                {typeahead.menu}
              </div>
              <MediaPreview attachment={media} />
            </div>
          </div>

          {error ? <p className="form-error">{error}</p> : null}

          <div className="reply-composer-actions">
            <div className="composer-tools">
              <MediaButton attachment={media} />
              <EmojiPicker onSelect={insertEmoji} />
            </div>
            <button
              className="primary-button compact"
              disabled={posting || empty || media.uploading}
            >
              {posting ? "Replying…" : "Reply"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function LinkPreviewCard({ preview }: { preview: LinkPreview }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [preview.url]);

  return (
    <a
      className="link-preview"
      href={preview.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
    >
      {preview.image && !imageFailed ? (
        <img
          className="link-preview-image"
          src={preview.image}
          alt=""
          loading="lazy"
          onError={() => setImageFailed(true)}
        />
      ) : null}
      <div className="link-preview-body">
        {preview.site_name ? (
          <span className="link-preview-site">{preview.site_name}</span>
        ) : null}
        <strong className="link-preview-title">{preview.title}</strong>
        {preview.description ? (
          <span className="link-preview-desc">{preview.description}</span>
        ) : null}
      </div>
    </a>
  );
}

/**
 * Remove the previewed URL from the end of the text (Twitter hides the URL that
 * produced the card). Only a trailing occurrence is stripped so a link in the
 * middle of a sentence stays readable.
 */
function textWithoutTrailingUrl(text: string, url: string): string {
  const trimmed = text.replace(/\s+$/, "");
  return trimmed.endsWith(url)
    ? trimmed.slice(0, trimmed.length - url.length).replace(/\s+$/, "")
    : text;
}

/**
 * Renders a post's text plus, when the first link has a preview, its unfurl
 * card. The fetch is owned here so the text and card stay in sync: once a
 * preview exists, the bare URL is dropped from the text (Twitter behaviour); if
 * there is no preview, the URL just stays as an inline link.
 */
export function PostBody({
  text,
  enablePreview = true,
}: {
  text: string;
  enablePreview?: boolean;
}) {
  const url = useMemo(
    () => (enablePreview ? firstPreviewableUrl(text) : null),
    [text, enablePreview],
  );
  const [preview, setPreview] = useState<LinkPreview | null>(null);

  useEffect(() => {
    if (!url) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreview(null);
    void unfurlUrl(url).then((result) => {
      if (!cancelled) {
        setPreview(result);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  const displayText = preview && url ? textWithoutTrailingUrl(text, url) : text;

  return (
    <>
      {displayText.trim() ? (
        <p>
          <RichContent text={displayText} />
        </p>
      ) : null}
      {preview ? <LinkPreviewCard preview={preview} /> : null}
    </>
  );
}

// The three audiences, in the order the composer lists them. ``trigger`` is the
// self-describing label on the composer button ("who can see this"); ``label`` /
// ``short`` / ``hint`` are used in the menu and the on-card badge.
const VISIBILITY_OPTIONS: {
  value: TweetVisibility;
  label: string;
  short: string;
  trigger: string;
  hint: string;
  Icon: typeof Globe;
}[] = [
  {
    value: "public",
    label: "Everyone",
    short: "Everyone",
    trigger: "Everyone can see",
    hint: "Anyone on Chirp",
    Icon: Globe,
  },
  {
    value: "followers",
    label: "Followers",
    short: "Followers",
    trigger: "Followers can see",
    hint: "Accounts that follow you",
    Icon: Users,
  },
  {
    value: "private",
    label: "Only you",
    short: "Only you",
    trigger: "Only you can see",
    hint: "Just you",
    Icon: Lock,
  },
];

/**
 * The composer's audience selector (Everyone / Followers / Only you): a pill
 * trigger and a custom popover menu, so it matches the app's other menus instead
 * of a native <select>.
 */
export function VisibilityPicker({
  value,
  onChange,
  disabled,
}: {
  value: TweetVisibility;
  onChange: (value: TweetVisibility) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active =
    VISIBILITY_OPTIONS.find((option) => option.value === value) ?? VISIBILITY_OPTIONS[0];

  useEffect(() => {
    if (!open) {
      return;
    }
    function onDocMouseDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function pick(next: TweetVisibility) {
    onChange(next);
    setOpen(false);
  }

  return (
    <div className="visibility-picker" ref={ref}>
      <button
        type="button"
        className="visibility-trigger"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Who can see this?"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((current) => !current);
        }}
      >
        <active.Icon size={16} aria-hidden="true" />
        <span>{active.trigger}</span>
        <ChevronDown size={16} aria-hidden="true" className="visibility-caret" />
      </button>
      {open ? (
        <div
          className="visibility-menu"
          role="menu"
          aria-label="Who can see this?"
          onClick={(event) => event.stopPropagation()}
        >
          <p className="visibility-menu-header">Who can see this?</p>
          {VISIBILITY_OPTIONS.map((option) => {
            const selected = option.value === value;
            return (
              <button
                key={option.value}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                className={selected ? "visibility-option active" : "visibility-option"}
                onClick={() => pick(option.value)}
              >
                <span className="visibility-option-icon">
                  <option.Icon size={18} aria-hidden="true" />
                </span>
                <span className="visibility-option-text">
                  <strong>{option.label}</strong>
                  <small>{option.hint}</small>
                </span>
                {selected ? (
                  <Check size={18} className="visibility-check" aria-hidden="true" />
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/** A small badge on a card marking a non-public tweet's audience. */
export function VisibilityBadge({ visibility }: { visibility: TweetVisibility }) {
  const option = VISIBILITY_OPTIONS.find((item) => item.value === visibility);
  if (!option || visibility === "public") {
    return null;
  }
  return (
    <span className="visibility-badge" title={`Visible to: ${option.label}`}>
      <option.Icon size={13} aria-hidden="true" />
      {option.short}
    </span>
  );
}

export function TweetCard({
  tweet,
  onOpen,
  onTweetPatch,
  currentUserId,
  onDeleted,
  onAuthorMuted,
  onAuthorBlocked,
  onQuoted,
}: {
  tweet: Tweet;
  onOpen: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
  currentUserId: number;
  onDeleted: (tweetId: number) => void;
  /**
   * Called after the author is muted from this card. Optional because muting
   * doesn't always hide content (a muted user's own profile still shows their
   * posts); hosts that aggregate posts -- the feed, a thread -- pass it to drop
   * the author. When omitted, the card simply stays.
   */
  onAuthorMuted?: (authorId: number) => void;
  /**
   * Called after the author is blocked from this card. A block always hides the
   * author, so hosts pass this to drop every post by them; when omitted it falls
   * back to removing just this card via onDeleted.
   */
  onAuthorBlocked?: (authorId: number) => void;
  onQuoted?: (tweet: Tweet) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editVisibility, setEditVisibility] = useState<TweetVisibility>(tweet.visibility);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmingBlock, setConfirmingBlock] = useState(false);
  const [moderating, setModerating] = useState(false);
  const [reporting, setReporting] = useState(false);
  const isOwn = tweet.author.id === currentUserId;

  async function muteAuthor() {
    setError("");
    try {
      await muteUser(tweet.author.id);
      onAuthorMuted?.(tweet.author.id);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function confirmBlock() {
    setModerating(true);
    setError("");
    try {
      await blockUser(tweet.author.id);
      if (onAuthorBlocked) {
        onAuthorBlocked(tweet.author.id);
      } else {
        onDeleted(tweet.id);
      }
    } catch (err) {
      setError(getErrorMessage(err));
      setModerating(false);
      setConfirmingBlock(false);
    }
  }

  async function saveEdit(content: string, mediaUrls: string[], mediaAlts: string[]) {
    setSaving(true);
    setError("");
    try {
      const updated = await editTweet(tweet.id, content, mediaUrls, mediaAlts, editVisibility);
      onTweetPatch(tweet.id, {
        content: updated.content,
        media_urls: updated.media_urls,
        edited_at: updated.edited_at,
        visibility: updated.visibility,
      });
      setEditing(false);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  function startEditing() {
    setEditVisibility(tweet.visibility);
    setEditing(true);
  }

  async function confirmDelete() {
    setDeleting(true);
    setError("");
    try {
      await deleteTweet(tweet.id);
      onDeleted(tweet.id);
    } catch (err) {
      setError(getErrorMessage(err));
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }

  const [commentOpen, setCommentOpen] = useState(false);
  const [acting, setActing] = useState<"like" | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [error, setError] = useState("");
  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(parseBackendDate(tweet.created_at));
  }, [tweet.created_at]);

  function handleQuoted(created: Tweet) {
    onTweetPatch(tweet.id, { retweet_count: tweet.retweet_count + 1 });
    onQuoted?.(created);
  }

  async function toggleLikeAction() {
    setActing("like");
    setError("");
    try {
      const result = await toggleTweetLike(tweet.id);
      onTweetPatch(tweet.id, {
        liked_by_me: result.liked,
        like_count: result.like_count,
      });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
  }

  // Errors propagate to ReplyComposer, which owns the draft and shows them
  // inline rather than on the card behind the modal.
  async function submitComment(content: string, mediaUrls: string[], mediaAlts: string[]) {
    await createComment(tweet.id, content, mediaUrls, mediaAlts);
    onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    // Only activate when the card itself is focused, not when typing in a
    // nested field (edit editor, inline comment box) or pressing a button.
    if (event.target !== event.currentTarget) {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDetail();
    }
  }

  // Clicks inside the post -- hashtags, links, avatar, username,
  // reply/repost/like -- count as engagements and bump the view count. A
  // plain click that bubbles up to the card and opens the detail page is not
  // counted here: the detail page records that view itself. The capture
  // handler fires before we know whether an inner element stops propagation,
  // so the post is deferred a tick and cancelled when the detail opens.
  //
  // The server collapses a user's repeat views inside a recent window, so
  // only the first engagement this card sees reports a view. The counter
  // shows the count the server persisted -- an optimistic +1 would lie
  // whenever this user had recently viewed the post, reverting on the next
  // refresh.
  const pendingEngagement = useRef(false);
  const viewRecorded = useRef(false);
  function queueEngagement() {
    if (editing || viewRecorded.current) return;
    pendingEngagement.current = true;
    window.setTimeout(() => {
      if (!pendingEngagement.current) return;
      pendingEngagement.current = false;
      viewRecorded.current = true;
      void recordPostViews([tweet.id]).then((counts) => {
        const updated = counts.find((item) => item.id === tweet.id);
        if (updated) {
          onTweetPatch(tweet.id, { view_count: updated.view_count });
        }
      });
    }, 0);
  }

  function openDetail() {
    pendingEngagement.current = false;
    // The detail page records this view; a later engagement on this card
    // would be the same user again anyway.
    viewRecorded.current = true;
    onOpen();
  }

  return (
    <article
      id={`post-${tweet.id}`}
      className="tweet-card clickable"
      role="button"
      tabIndex={0}
      onClick={openDetail}
      onKeyDown={handleKeyDown}
      aria-label={`Open tweet by ${tweet.author.username}`}
    >
      <Link
        to={`/${encodeURIComponent(tweet.author.username)}`}
        className="author-link"
        onClick={(event) => {
          event.stopPropagation();
          queueEngagement();
        }}
        aria-label={`View profile of ${tweet.author.username}`}
      >
        <Avatar user={tweet.author} />
      </Link>
      {isOwn ? (
        <PostMenu
          onEdit={startEditing}
          onDelete={() => setConfirmingDelete(true)}
        />
      ) : (
        <PostMenu
          authorUsername={tweet.author.username}
          onMute={() => void muteAuthor()}
          onBlock={() => setConfirmingBlock(true)}
          onReport={() => setReporting(true)}
        />
      )}
      <div className="tweet-body" onClickCapture={queueEngagement}>
        <header>
          <Link
            to={`/${encodeURIComponent(tweet.author.username)}`}
            className="author-link"
            onClick={(event) => event.stopPropagation()}
          >
            <strong>{displayName(tweet.author)}</strong>
          </Link>
          <span>@{tweet.author.username}</span>
          <span>{displayDate}</span>
          {tweet.edited_at ? <span className="edited-tag">· edited</span> : null}
          <VisibilityBadge visibility={tweet.visibility} />
        </header>
        <PostBody text={tweet.content} enablePreview={tweet.media_urls.length === 0} />
        {editing ? (
          <PostEditor
            initialContent={tweet.content}
            initialMedia={tweet.media_urls}
            initialAlts={tweet.media_alts}
            maxLength={280}
            saving={saving}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
            visibility={editVisibility}
            onVisibilityChange={setEditVisibility}
          />
        ) : null}
        {tweet.media_urls.length > 0 ? (
          <MediaGallery urls={tweet.media_urls} alts={tweet.media_alts} />
        ) : null}
        {tweet.quoted_post ? <QuotedPostCard post={tweet.quoted_post} /> : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <footer className="tweet-actions">
          <button
            className="tweet-action comment"
            onClick={(event) => {
              event.stopPropagation();
              setCommentOpen(true);
            }}
            aria-label="Reply"
          >
            <MessageCircle size={18} aria-hidden="true" />
            <span>{tweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={(event) => {
              event.stopPropagation();
              setQuoting(true);
            }}
            aria-label="Quote"
          >
            <Repeat2 size={18} aria-hidden="true" />
            <span>{tweet.retweet_count}</span>
          </button>
          <button
            className={tweet.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={(event) => {
              event.stopPropagation();
              void toggleLikeAction();
            }}
            disabled={acting === "like"}
            aria-pressed={tweet.liked_by_me}
          >
            <Heart size={18} aria-hidden="true" fill={tweet.liked_by_me ? "currentColor" : "none"} />
            <span>{tweet.like_count}</span>
          </button>
          <span className="tweet-action views" aria-label="Views">
            <BarChart2 size={18} aria-hidden="true" />
            <span>{tweet.view_count}</span>
          </span>
        </footer>
      </div>
      {commentOpen ? (
        <ReplyComposer
          target={tweet}
          onClose={() => setCommentOpen(false)}
          onSubmit={submitComment}
        />
      ) : null}
      {confirmingDelete ? (
        <ConfirmDialog
          title="Delete Tweet?"
          message="This can't be undone and it will be removed from your profile, the timeline, and any threads it started."
          confirmLabel="Delete"
          busyLabel="Deleting…"
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}
      {quoting ? (
        <QuoteComposer
          quoted={tweet}
          onClose={() => setQuoting(false)}
          onQuoted={handleQuoted}
        />
      ) : null}
      {confirmingBlock ? (
        <ConfirmDialog
          title={`Block @${tweet.author.username}?`}
          message="They won't be able to follow you or see your Tweets, and you won't see theirs. Any follow between you is removed."
          confirmLabel="Block"
          busyLabel="Blocking…"
          busy={moderating}
          onConfirm={() => void confirmBlock()}
          onCancel={() => setConfirmingBlock(false)}
        />
      ) : null}
      {reporting ? (
        <ReportModal
          postId={tweet.id}
          username={tweet.author.username}
          onClose={() => setReporting(false)}
        />
      ) : null}
    </article>
  );
}

export function CommentCard({
  comment,
  onChanged,
  onReplyCreated,
  currentUserId,
  depth = 0,
  onOpen,
}: {
  comment: Comment;
  onChanged: () => void;
  onReplyCreated: () => void;
  currentUserId: number;
  depth?: number;
  onOpen?: () => void;
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [localComment, setLocalComment] = useState(comment);
  const [acting, setActing] = useState<"like" | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmingBlock, setConfirmingBlock] = useState(false);
  const [moderating, setModerating] = useState(false);
  const [reporting, setReporting] = useState(false);
  const isOwn = localComment.author.id === currentUserId;

  useEffect(() => {
    setLocalComment(comment);
  }, [comment]);

  // Muting or blocking the author removes their comments from the thread; a
  // reload re-fetches it with them filtered out server-side.
  async function muteAuthor() {
    setError("");
    try {
      await muteUser(localComment.author.id);
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function confirmBlock() {
    setModerating(true);
    setError("");
    try {
      await blockUser(localComment.author.id);
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
      setModerating(false);
      setConfirmingBlock(false);
    }
  }

  async function saveEdit(content: string, mediaUrls: string[], mediaAlts: string[]) {
    setSaving(true);
    setError("");
    try {
      const updated = await editComment(localComment.id, content, mediaUrls, mediaAlts);
      setLocalComment((value) => ({
        ...value,
        content: updated.content,
        media_urls: updated.media_urls,
        edited_at: updated.edited_at,
      }));
      setEditing(false);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    setError("");
    try {
      await deleteComment(localComment.id);
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }

  function handleQuoted() {
    setLocalComment((value) => ({
      ...value,
      retweet_count: value.retweet_count + 1,
    }));
  }

  async function toggleCommentLikeAction() {
    setActing("like");
    setError("");
    try {
      const result = await toggleCommentLike(localComment.id);
      setLocalComment((value) => ({
        ...value,
        liked_by_me: result.liked,
        like_count: result.like_count,
      }));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
  }

  // Errors propagate to ReplyComposer, which shows them inside the modal.
  async function submitReply(content: string, mediaUrls: string[], mediaAlts: string[]) {
    await replyToComment(localComment.id, content, mediaUrls, mediaAlts);
    setLocalComment((value) => ({
      ...value,
      comment_count: value.comment_count + 1,
    }));
    onReplyCreated();
    onChanged();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!onOpen || event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openThread();
    }
  }

  // Clicks inside the comment count as engagements and bump the view count.
  // A plain click that bubbles up and opens the thread is not counted here:
  // the detail page records that view. The capture handler fires before we
  // know whether an inner element stops propagation, so the post is deferred
  // a tick and cancelled when the thread opens.
  //
  // The server collapses a user's repeat views inside a recent window, so
  // only the first engagement this card sees reports a view. The counter
  // shows the count the server persisted -- an optimistic +1 would lie
  // whenever this user had recently viewed the comment, reverting on the
  // next refresh.
  const pendingEngagement = useRef(false);
  const viewRecorded = useRef(false);
  function queueEngagement() {
    if (editing || viewRecorded.current) return;
    pendingEngagement.current = true;
    window.setTimeout(() => {
      if (!pendingEngagement.current) return;
      pendingEngagement.current = false;
      viewRecorded.current = true;
      void recordPostViews([localComment.id]).then((counts) => {
        const updated = counts.find((item) => item.id === localComment.id);
        if (updated) {
          setLocalComment((value) => ({ ...value, view_count: updated.view_count }));
        }
      });
    }, 0);
  }

  function openThread() {
    if (!onOpen) return;
    pendingEngagement.current = false;
    // The detail page records this view; a later engagement on this card
    // would be the same user again anyway.
    viewRecorded.current = true;
    onOpen();
  }

  return (
    <article
      id={`post-${localComment.id}`}
      className={`${localComment.parent_comment_id ? "comment-card reply" : "comment-card"}${onOpen ? " clickable" : ""}`}
      style={depth > 0 ? { paddingLeft: 18 + Math.min(depth, 8) * 22 } : undefined}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onClick={onOpen ? openThread : undefined}
      onKeyDown={onOpen ? handleKeyDown : undefined}
    >
      <Link
        to={`/${encodeURIComponent(localComment.author.username)}`}
        className="author-link"
        onClick={(event) => {
          event.stopPropagation();
          queueEngagement();
        }}
        aria-label={`View profile of ${localComment.author.username}`}
      >
        <Avatar user={localComment.author} size="small" />
      </Link>
      {isOwn ? (
        <PostMenu
          onEdit={() => setEditing(true)}
          onDelete={() => setConfirmingDelete(true)}
        />
      ) : (
        <PostMenu
          authorUsername={localComment.author.username}
          onMute={() => void muteAuthor()}
          onBlock={() => setConfirmingBlock(true)}
          onReport={() => setReporting(true)}
        />
      )}
      <div className="comment-body" onClickCapture={queueEngagement}>
        <header>
          <Link
            to={`/${encodeURIComponent(localComment.author.username)}`}
            className="author-link"
            onClick={(event) => event.stopPropagation()}
          >
            <strong>{displayName(localComment.author)}</strong>
          </Link>
          <span>@{localComment.author.username}</span>
          <span>{formatCompactDate(localComment.created_at)}</span>
          {localComment.parent_comment_id ? <span>Reply</span> : null}
          {localComment.edited_at ? <span className="edited-tag">· edited</span> : null}
        </header>
        <PostBody
          text={localComment.content}
          enablePreview={localComment.media_urls.length === 0}
        />
        {editing ? (
          <PostEditor
            initialContent={localComment.content}
            initialMedia={localComment.media_urls}
            initialAlts={localComment.media_alts}
            maxLength={1000}
            saving={saving}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
          />
        ) : null}
        {localComment.media_urls.length > 0 ? (
          <MediaGallery urls={localComment.media_urls} alts={localComment.media_alts} />
        ) : null}
        {localComment.quoted_post ? (
          <QuotedPostCard post={localComment.quoted_post} />
        ) : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <footer className="tweet-actions comment-actions">
          <button
            className="tweet-action comment"
            onClick={(event) => {
              event.stopPropagation();
              setReplyOpen(true);
            }}
            aria-label="Reply"
          >
            <MessageCircle size={16} aria-hidden="true" />
            <span>{localComment.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={(event) => {
              event.stopPropagation();
              setQuoting(true);
            }}
            aria-label="Quote"
          >
            <Repeat2 size={16} aria-hidden="true" />
            <span>{localComment.retweet_count}</span>
          </button>
          <button
            className={localComment.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={(event) => {
              event.stopPropagation();
              void toggleCommentLikeAction();
            }}
            disabled={acting === "like"}
            aria-pressed={localComment.liked_by_me}
          >
            <Heart
              size={16}
              aria-hidden="true"
              fill={localComment.liked_by_me ? "currentColor" : "none"}
            />
            <span>{localComment.like_count}</span>
          </button>
          <span className="tweet-action views" aria-label="Views">
            <BarChart2 size={16} aria-hidden="true" />
            <span>{localComment.view_count}</span>
          </span>
        </footer>
      </div>
      {replyOpen ? (
        <ReplyComposer
          target={localComment}
          onClose={() => setReplyOpen(false)}
          onSubmit={submitReply}
        />
      ) : null}
      {confirmingDelete ? (
        <ConfirmDialog
          title="Delete comment?"
          message="This can't be undone and it will remove this comment and any replies to it."
          confirmLabel="Delete"
          busyLabel="Deleting…"
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}
      {quoting ? (
        <QuoteComposer
          quoted={localComment}
          onClose={() => setQuoting(false)}
          onQuoted={handleQuoted}
        />
      ) : null}
      {confirmingBlock ? (
        <ConfirmDialog
          title={`Block @${localComment.author.username}?`}
          message="They won't be able to follow you or see your Tweets, and you won't see theirs. Any follow between you is removed."
          confirmLabel="Block"
          busyLabel="Blocking…"
          busy={moderating}
          onConfirm={() => void confirmBlock()}
          onCancel={() => setConfirmingBlock(false)}
        />
      ) : null}
      {reporting ? (
        <ReportModal
          postId={localComment.id}
          username={localComment.author.username}
          onClose={() => setReporting(false)}
        />
      ) : null}
    </article>
  );
}

export function mergeTweetStats(
  current: Record<number, Tweet>,
  stats: TweetStats[],
): Record<number, Tweet> {
  let changed = false;
  const next = { ...current };

  for (const item of stats) {
    const tweet = current[item.id];
    if (!tweet) {
      continue;
    }

    if (
      tweet.like_count !== item.like_count ||
      tweet.comment_count !== item.comment_count ||
      tweet.retweet_count !== item.retweet_count ||
      tweet.view_count !== item.view_count ||
      tweet.liked_by_me !== item.liked_by_me
    ) {
      next[item.id] = {
        ...tweet,
        like_count: item.like_count,
        comment_count: item.comment_count,
        retweet_count: item.retweet_count,
        view_count: item.view_count,
        liked_by_me: item.liked_by_me,
      };
      changed = true;
    }
  }

  return changed ? next : current;
}

export function mergeCommentStats(current: Comment[], stats: CommentStats[]): Comment[] {
  if (stats.length === 0) {
    return current;
  }

  let changed = false;
  const statsById = new Map(stats.map((item) => [item.id, item]));
  const next = current.map((comment) => {
    const item = statsById.get(comment.id);
    if (!item) {
      return comment;
    }

    if (
      comment.like_count === item.like_count &&
      comment.comment_count === item.comment_count &&
      comment.retweet_count === item.retweet_count &&
      comment.view_count === item.view_count &&
      comment.liked_by_me === item.liked_by_me
    ) {
      return comment;
    }

    changed = true;
    return {
      ...comment,
      like_count: item.like_count,
      comment_count: item.comment_count,
      retweet_count: item.retweet_count,
      view_count: item.view_count,
      liked_by_me: item.liked_by_me,
    };
  });

  return changed ? next : current;
}

export function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) {
    return err.message;
  }
  return "Something went wrong.";
}

/** 6 800 → "6.8K": the compact form the profile stat row uses. */
export function formatCompactCount(value: number): string {
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatCompactDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parseBackendDate(value));
}

export function parseBackendDate(value: string): Date {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}
