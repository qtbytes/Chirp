import {
  FormEvent,
  KeyboardEvent,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Heart,
  Image as ImageIcon,
  Loader2,
  MessageCircle,
  MoreHorizontal,
  Pencil,
  Repeat2,
  Trash2,
  X,
} from "lucide-react";
import {
  ApiError,
  createComment,
  createTweet,
  deleteComment,
  deleteTweet,
  displayName,
  editComment,
  editTweet,
  isVideoUrl,
  replyToComment,
  resolveMediaUrl,
  toggleCommentLike,
  toggleTweetLike,
  unfurlUrl,
} from "./api";
import type {
  Comment,
  CommentStats,
  LinkPreview,
  QuotedPost,
  Tweet,
  TweetStats,
  UserSummary,
} from "./types";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import { ACCEPTED_MEDIA, useMediaAttachment, type MediaAttachment } from "./useMediaAttachment";

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

type ContentToken = { type: "text" | "url"; value: string };

function tokenizeContent(text: string): ContentToken[] {
  const tokens: ContentToken[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(URL_REGEX)) {
    const start = match.index ?? 0;
    let url = match[0];
    // Keep trailing punctuation as plain text so "see http://x.com." works.
    const trailing = url.match(TRAILING_PUNCTUATION)?.[0] ?? "";
    if (trailing) {
      url = url.slice(0, url.length - trailing.length);
    }
    if (start > lastIndex) {
      tokens.push({ type: "text", value: text.slice(lastIndex, start) });
    }
    tokens.push({ type: "url", value: url });
    if (trailing) {
      tokens.push({ type: "text", value: trailing });
    }
    lastIndex = start + match[0].length;
  }
  if (lastIndex < text.length) {
    tokens.push({ type: "text", value: text.slice(lastIndex) });
  }
  return tokens;
}

function InlineImage({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);

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
    <a
      className="tweet-media-link"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
    >
      <img
        className="tweet-media-image"
        src={url}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </a>
  );
}

// Renders user content as text, turning URLs into links and image URLs into
// inline images. Only http(s) URLs are matched, so no javascript: injection.
export function RichContent({ text }: { text: string }) {
  const tokens = useMemo(() => tokenizeContent(text), [text]);

  return (
    <>
      {tokens.map((token, index) => {
        if (token.type === "text") {
          return token.value;
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

// Renders a tweet/comment's uploaded media (media_urls), resolved to absolute
// URLs. Images are clickable through to the full file; videos play inline.
// Lays out as a 2-column grid when there is more than one item.
export function MediaGallery({ urls }: { urls: string[] }) {
  const items = urls
    .map((url) => resolveMediaUrl(url))
    .filter((src): src is string => Boolean(src));
  if (items.length === 0) {
    return null;
  }

  if (items.length === 1) {
    const src = items[0];
    return (
      <div className="tweet-media-gallery">
        {isVideoUrl(src) ? (
          <VideoPlayer src={src} className="tweet-media-video" />
        ) : (
          <a
            className="tweet-media-link"
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            <img className="tweet-media-image" src={src} alt="" loading="lazy" />
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="media-grid" data-count={items.length}>
      {items.map((src) =>
        isVideoUrl(src) ? (
          <div key={src} className="media-grid__cell">
            <VideoPlayer src={src} className="media-grid__video" />
          </div>
        ) : (
          <a
            key={src}
            className="media-grid__cell"
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            <img src={src} alt="" loading="lazy" />
          </a>
        ),
      )}
    </div>
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
export function MediaPreview({ attachment }: { attachment: MediaAttachment }) {
  if (
    attachment.mediaUrls.length === 0 &&
    !attachment.uploading &&
    !attachment.error
  ) {
    return null;
  }

  const count = attachment.mediaUrls.length;

  const removeButton = (url: string) => (
    <button
      type="button"
      className="composer-media-remove"
      onClick={(event) => {
        event.stopPropagation();
        attachment.remove(url);
      }}
      aria-label="Remove media"
    >
      <X size={16} aria-hidden="true" />
    </button>
  );

  return (
    <div className="composer-media">
      {count === 1 ? (
        <div className="composer-media-item">
          {resolveMediaUrl(attachment.mediaUrls[0]) ? (
            isVideoUrl(attachment.mediaUrls[0]) ? (
              <VideoPlayer
                src={resolveMediaUrl(attachment.mediaUrls[0])!}
                className="composer-media-image"
              />
            ) : (
              <img
                className="composer-media-image"
                src={resolveMediaUrl(attachment.mediaUrls[0])!}
                alt="Attached preview"
              />
            )
          ) : null}
          {removeButton(attachment.mediaUrls[0])}
        </div>
      ) : count > 1 ? (
        <div className="media-grid" data-count={count}>
          {attachment.mediaUrls.map((url) => {
            const src = resolveMediaUrl(url);
            return (
              <div className="media-grid__cell" key={url}>
                {src ? (
                  isVideoUrl(url) ? (
                    <VideoPlayer src={src} className="media-grid__video" />
                  ) : (
                    <img src={src} alt="Attached preview" />
                  )
                ) : null}
                {removeButton(url)}
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
    </div>
  );
}

// A "⋯" dropdown with Edit/Delete, shown only on the author's own posts.
export function PostMenu({
  onEdit,
  onDelete,
}: {
  onEdit: () => void;
  onDelete: () => void;
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
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          >
            <Pencil size={16} aria-hidden="true" />
            <span>Edit</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="danger"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          >
            <Trash2 size={16} aria-hidden="true" />
            <span>Delete</span>
          </button>
        </div>
      ) : null}
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

// Inline editor that replaces a post's content while editing. Media is
// preserved as-is (the caller re-sends the existing media on save).
export function PostEditor({
  initialContent,
  maxLength,
  canSaveEmpty,
  saving,
  onSave,
  onCancel,
}: {
  initialContent: string;
  maxLength: number;
  canSaveEmpty: boolean;
  saving: boolean;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialContent);
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    value,
    setValue,
    maxLength,
  );
  const canSave = (value.trim().length > 0 || canSaveEmpty) && !saving;

  return (
    <form
      className="post-editor"
      onClick={(event) => event.stopPropagation()}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) {
          onSave(value.trim());
        }
      }}
    >
      <textarea
        {...fieldProps}
        value={value}
        maxLength={maxLength}
        aria-label="Edit content"
        autoFocus
      />
      <div className="post-editor-actions">
        <EmojiPicker onSelect={insertEmoji} />
        <span className="post-editor-spacer" />
        <button
          type="button"
          className="outline-button compact"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button className="primary-button compact" disabled={!canSave}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
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
      {post.media_urls.length > 0 ? <MediaGallery urls={post.media_urls} /> : null}
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
  const [content, setContent] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    content,
    setContent,
    280,
  );
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
      const tweet = await createTweet(content.trim(), media.mediaUrls, quoted.id);
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
          <textarea
            {...fieldProps}
            value={content}
            maxLength={280}
            placeholder="Add a comment"
            aria-label="Quote comment"
            autoFocus
          />
          <MediaPreview attachment={media} />
          <QuotedPostCard post={quoted} preview />
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
  onSubmit: (content: string, mediaUrls: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const currentUser = useCurrentUser();
  const [content, setContent] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    content,
    setContent,
    1000,
  );
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
      await onSubmit(content.trim(), media.mediaUrls);
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
                <MediaGallery urls={target.media_urls} />
              ) : null}
              <p className="reply-target-replying">
                Replying to <span className="reply-mention">@{target.author.username}</span>
              </p>
            </div>
          </div>

          <div className="reply-input-row">
            {currentUser ? <Avatar user={currentUser} /> : null}
            <div className="reply-input-body">
              <textarea
                {...fieldProps}
                value={content}
                maxLength={1000}
                placeholder="Post your reply"
                aria-label="Post your reply"
                autoFocus
              />
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

export function TweetCard({
  tweet,
  onOpen,
  onTweetPatch,
  currentUserId,
  onDeleted,
  onQuoted,
}: {
  tweet: Tweet;
  onOpen: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
  currentUserId: number;
  onDeleted: (tweetId: number) => void;
  onQuoted?: (tweet: Tweet) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const isOwn = tweet.author.id === currentUserId;

  async function saveEdit(content: string) {
    setSaving(true);
    setError("");
    try {
      const updated = await editTweet(tweet.id, content, tweet.media_urls);
      onTweetPatch(tweet.id, {
        content: updated.content,
        media_urls: updated.media_urls,
        edited_at: updated.edited_at,
      });
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
  async function submitComment(content: string, mediaUrls: string[]) {
    await createComment(tweet.id, content, mediaUrls);
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
      onOpen();
    }
  }

  return (
    <article
      className="tweet-card clickable"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      aria-label={`Open tweet by ${tweet.author.username}`}
    >
      <Link
        to={`/${encodeURIComponent(tweet.author.username)}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${tweet.author.username}`}
      >
        <Avatar user={tweet.author} />
      </Link>
      {isOwn ? (
        <PostMenu
          onEdit={() => setEditing(true)}
          onDelete={() => setConfirmingDelete(true)}
        />
      ) : null}
      <div className="tweet-body">
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
        </header>
        {editing ? (
          <PostEditor
            initialContent={tweet.content}
            maxLength={280}
            canSaveEmpty={tweet.media_urls.length > 0}
            saving={saving}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <PostBody text={tweet.content} enablePreview={tweet.media_urls.length === 0} />
        )}
        {tweet.media_urls.length > 0 ? <MediaGallery urls={tweet.media_urls} /> : null}
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
    </article>
  );
}

export function CommentCard({
  comment,
  onChanged,
  onReplyCreated,
  currentUserId,
  depth = 0,
}: {
  comment: Comment;
  onChanged: () => void;
  onReplyCreated: () => void;
  currentUserId: number;
  depth?: number;
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
  const isOwn = localComment.author.id === currentUserId;

  useEffect(() => {
    setLocalComment(comment);
  }, [comment]);

  async function saveEdit(content: string) {
    setSaving(true);
    setError("");
    try {
      const updated = await editComment(
        localComment.id,
        content,
        localComment.media_urls,
      );
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
  async function submitReply(content: string, mediaUrls: string[]) {
    await replyToComment(localComment.id, content, mediaUrls);
    setLocalComment((value) => ({
      ...value,
      comment_count: value.comment_count + 1,
    }));
    onReplyCreated();
    onChanged();
  }

  return (
    <article
      id={`post-${localComment.id}`}
      className={localComment.parent_comment_id ? "comment-card reply" : "comment-card"}
      style={depth > 0 ? { paddingLeft: 18 + Math.min(depth, 8) * 22 } : undefined}
    >
      <Link
        to={`/${encodeURIComponent(localComment.author.username)}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${localComment.author.username}`}
      >
        <Avatar user={localComment.author} size="small" />
      </Link>
      {isOwn ? (
        <PostMenu
          onEdit={() => setEditing(true)}
          onDelete={() => setConfirmingDelete(true)}
        />
      ) : null}
      <div className="comment-body">
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
        {editing ? (
          <PostEditor
            initialContent={localComment.content}
            maxLength={1000}
            canSaveEmpty={localComment.media_urls.length > 0}
            saving={saving}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <PostBody
            text={localComment.content}
            enablePreview={localComment.media_urls.length === 0}
          />
        )}
        {localComment.media_urls.length > 0 ? <MediaGallery urls={localComment.media_urls} /> : null}
        {localComment.quoted_post ? (
          <QuotedPostCard post={localComment.quoted_post} />
        ) : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <footer className="tweet-actions comment-actions">
          <button
            className="tweet-action comment"
            onClick={() => setReplyOpen(true)}
            aria-label="Reply"
          >
            <MessageCircle size={16} aria-hidden="true" />
            <span>{localComment.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={() => setQuoting(true)}
            aria-label="Quote"
          >
            <Repeat2 size={16} aria-hidden="true" />
            <span>{localComment.retweet_count}</span>
          </button>
          <button
            className={localComment.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={() => void toggleCommentLikeAction()}
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
      tweet.liked_by_me !== item.liked_by_me
    ) {
      next[item.id] = {
        ...tweet,
        like_count: item.like_count,
        comment_count: item.comment_count,
        retweet_count: item.retweet_count,
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
