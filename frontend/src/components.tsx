import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Heart, Image as ImageIcon, Loader2, MessageCircle, Repeat2, X } from "lucide-react";
import {
  ApiError,
  createComment,
  displayName,
  isVideoUrl,
  replyToComment,
  resolveMediaUrl,
  retweetComment,
  retweetTweet,
  toggleCommentLike,
  toggleTweetLike,
} from "./api";
import type { Comment, CommentStats, Tweet, TweetStats, UserSummary } from "./types";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import { ACCEPTED_MEDIA, useMediaAttachment, type MediaAttachment } from "./useMediaAttachment";

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

export function TweetCard({
  tweet,
  onOpen,
  onTweetPatch,
}: {
  tweet: Tweet;
  onOpen: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
}) {
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLInputElement>(comment, setComment, 1000);
  const media = useMediaAttachment();
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");
  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(parseBackendDate(tweet.created_at));
  }, [tweet.created_at]);

  async function runRetweetAction() {
    setActing("retweet");
    setError("");
    try {
      const result = await retweetTweet(tweet.id);
      if (result.created) {
        onTweetPatch(tweet.id, { retweet_count: tweet.retweet_count + 1 });
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
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

  async function submitComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((!comment.trim() && media.mediaUrls.length === 0) || media.uploading) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await createComment(tweet.id, comment.trim(), media.mediaUrls);
      setComment("");
      media.clear();
      setCommentOpen(false);
      onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
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
      <div className="tweet-body">
        {tweet.retweeted_by ? (
          <p className="retweet-banner">
            <Repeat2 size={14} aria-hidden="true" />
            <span>{displayName(tweet.retweeted_by)} retweeted</span>
          </p>
        ) : null}
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
        </header>
        <p><RichContent text={tweet.content} /></p>
        {tweet.media_urls.length > 0 ? <MediaGallery urls={tweet.media_urls} /> : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <footer className="tweet-actions">
          <button
            className="tweet-action comment"
            onClick={(event) => {
              event.stopPropagation();
              setCommentOpen((open) => !open);
            }}
            aria-expanded={commentOpen}
          >
            <MessageCircle size={18} aria-hidden="true" />
            <span>{tweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={(event) => {
              event.stopPropagation();
              void runRetweetAction();
            }}
            disabled={acting === "retweet"}
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
        {commentOpen ? (
          <form
            className="comment-form"
            onClick={(event) => event.stopPropagation()}
            onSubmit={submitComment}
          >
            <div className="composer-tools">
              <EmojiPicker onSelect={insertEmoji} />
              <MediaButton attachment={media} />
            </div>
            <input
              {...fieldProps}
              value={comment}
              maxLength={1000}
              placeholder="Post your reply"
              aria-label="Comment"
            />
            <button
              className="primary-button compact"
              disabled={acting === "comment" || (!comment.trim() && media.mediaUrls.length === 0) || media.uploading}
            >
              Reply
            </button>
            <MediaPreview attachment={media} />
          </form>
        ) : null}
      </div>
    </article>
  );
}

export function CommentCard({
  comment,
  onChanged,
  onReplyCreated,
}: {
  comment: Comment;
  onChanged: () => void;
  onReplyCreated: () => void;
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState("");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLInputElement>(reply, setReply, 1000);
  const media = useMediaAttachment();
  const [localComment, setLocalComment] = useState(comment);
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setLocalComment(comment);
  }, [comment]);

  async function runCommentRetweetAction(task: () => Promise<void>) {
    setActing("retweet");
    setError("");
    try {
      await task();
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
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

  async function submitReply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((!reply.trim() && media.mediaUrls.length === 0) || media.uploading) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await replyToComment(localComment.id, reply.trim(), media.mediaUrls);
      setReply("");
      media.clear();
      setReplyOpen(false);
      setLocalComment((value) => ({
        ...value,
        comment_count: value.comment_count + 1,
      }));
      onReplyCreated();
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
  }

  return (
    <article
      id={`post-${localComment.id}`}
      className={localComment.parent_comment_id ? "comment-card reply" : "comment-card"}
    >
      <Link
        to={`/${encodeURIComponent(localComment.author.username)}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${localComment.author.username}`}
      >
        <Avatar user={localComment.author} size="small" />
      </Link>
      <div className="comment-body">
        {localComment.retweeted_by ? (
          <p className="retweet-banner">
            <Repeat2 size={14} aria-hidden="true" />
            <span>{displayName(localComment.retweeted_by)} retweeted</span>
          </p>
        ) : null}
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
        </header>
        <p><RichContent text={localComment.content} /></p>
        {localComment.media_urls.length > 0 ? <MediaGallery urls={localComment.media_urls} /> : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <footer className="tweet-actions comment-actions">
          <button
            className="tweet-action comment"
            onClick={() => setReplyOpen((open) => !open)}
            aria-expanded={replyOpen}
          >
            <MessageCircle size={16} aria-hidden="true" />
            <span>{localComment.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={() =>
              void runCommentRetweetAction(() => retweetComment(localComment.id))
            }
            disabled={acting === "retweet"}
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
        {replyOpen ? (
          <form className="comment-form comment-reply-form" onSubmit={submitReply}>
            <div className="composer-tools">
              <EmojiPicker onSelect={insertEmoji} />
              <MediaButton attachment={media} />
            </div>
            <input
              {...fieldProps}
              value={reply}
              maxLength={1000}
              placeholder="Reply to this comment"
              aria-label="Reply to comment"
            />
            <button
              className="primary-button compact"
              disabled={acting === "comment" || (!reply.trim() && media.mediaUrls.length === 0) || media.uploading}
            >
              Reply
            </button>
            <MediaPreview attachment={media} />
          </form>
        ) : null}
      </div>
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
