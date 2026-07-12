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
  Ban,
  Check,
  ChevronDown,
  Flag,
  Globe,
  Heart,
  Image as ImageIcon,
  Loader2,
  Lock,
  MessageCircle,
  MoreHorizontal,
  Pencil,
  Repeat2,
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
  replyToComment,
  reportPost,
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
  ReportReason,
  Tweet,
  TweetStats,
  TweetVisibility,
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

// Modal for reporting a post: pick a reason, add optional detail, submit. On
// success it swaps to a confirmation -- a report is a moderation signal, so it
// deliberately does not hide the post from the reporter afterwards.
export function ReportModal({
  postId,
  authorUsername,
  onClose,
}: {
  postId: number;
  authorUsername: string;
  onClose: () => void;
}) {
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
      await reportPost(postId, reason, details);
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
        aria-label={`Report @${authorUsername}'s post`}
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
            <h2>Report post</h2>
            <p className="report-modal-intro">
              Why are you reporting @{authorUsername}&apos;s post?
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

// Inline editor that replaces a post's content while editing. Its action bar
// mirrors the composer -- emoji + media tools on the left, an optional audience
// row above them (tweets only) -- so editing feels the same as posting. Media is
// seeded from the post and can be added to or removed before saving.
export function PostEditor({
  initialContent,
  initialMedia = [],
  maxLength,
  saving,
  onSave,
  onCancel,
  visibility,
  onVisibilityChange,
}: {
  initialContent: string;
  initialMedia?: string[];
  maxLength: number;
  saving: boolean;
  onSave: (content: string, mediaUrls: string[]) => void;
  onCancel: () => void;
  // When both are given (tweet edit), the editor shows an audience selector.
  // Omitted for comments, which have no audience of their own.
  visibility?: TweetVisibility;
  onVisibilityChange?: (value: TweetVisibility) => void;
}) {
  const [value, setValue] = useState(initialContent);
  const media = useMediaAttachment(initialMedia);
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    value,
    setValue,
    maxLength,
  );
  const canSave =
    (value.trim().length > 0 || media.mediaUrls.length > 0) && !saving && !media.uploading;

  return (
    <form
      className="post-editor"
      onClick={(event) => event.stopPropagation()}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) {
          onSave(value.trim(), media.mediaUrls);
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
      <MediaPreview attachment={media} />
      {media.error ? <p className="form-error">{media.error}</p> : null}
      {visibility != null && onVisibilityChange ? (
        <div className="composer-visibility">
          <VisibilityPicker
            value={visibility}
            onChange={onVisibilityChange}
            disabled={saving}
          />
        </div>
      ) : null}
      <div className="post-editor-actions">
        <div className="composer-tools">
          <EmojiPicker onSelect={insertEmoji} />
          <MediaButton attachment={media} />
        </div>
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

  async function saveEdit(content: string, mediaUrls: string[]) {
    setSaving(true);
    setError("");
    try {
      const updated = await editTweet(tweet.id, content, mediaUrls, editVisibility);
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
          <VisibilityBadge visibility={tweet.visibility} />
        </header>
        {editing ? (
          <PostEditor
            initialContent={tweet.content}
            initialMedia={tweet.media_urls}
            maxLength={280}
            saving={saving}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
            visibility={editVisibility}
            onVisibilityChange={setEditVisibility}
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
          authorUsername={tweet.author.username}
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

  async function saveEdit(content: string, mediaUrls: string[]) {
    setSaving(true);
    setError("");
    try {
      const updated = await editComment(localComment.id, content, mediaUrls);
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
      ) : (
        <PostMenu
          authorUsername={localComment.author.username}
          onMute={() => void muteAuthor()}
          onBlock={() => setConfirmingBlock(true)}
          onReport={() => setReporting(true)}
        />
      )}
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
            initialMedia={localComment.media_urls}
            maxLength={1000}
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
          authorUsername={localComment.author.username}
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
