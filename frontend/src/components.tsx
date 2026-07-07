import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Heart, Image as ImageIcon, Loader2, MessageCircle, Repeat2, X } from "lucide-react";
import {
  ApiError,
  createComment,
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

// Renders a tweet/comment's uploaded image (media_url), resolved to an
// absolute URL, clickable through to the full image.
export function MediaAttachmentView({ url }: { url: string }) {
  const src = resolveMediaUrl(url);
  if (!src) {
    return null;
  }
  return (
    <a
      className="tweet-media-link"
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
    >
      <img className="tweet-media-image" src={src} alt="" loading="lazy" />
    </a>
  );
}

// Composer control: the "add image" button plus its hidden file input.
export function MediaButton({ attachment }: { attachment: MediaAttachment }) {
  return (
    <>
      <input
        ref={attachment.inputRef}
        type="file"
        accept={ACCEPTED_MEDIA}
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
        disabled={attachment.uploading}
        aria-label="Add image"
        title="Add image"
      >
        <ImageIcon size={20} aria-hidden="true" />
      </button>
    </>
  );
}

// Composer preview: shows the pending upload / attached image with a remove
// control, plus any upload error.
export function MediaPreview({ attachment }: { attachment: MediaAttachment }) {
  const src = attachment.mediaUrl ? resolveMediaUrl(attachment.mediaUrl) : null;

  if (!attachment.uploading && !src && !attachment.error) {
    return null;
  }

  return (
    <div className="composer-media">
      {attachment.uploading ? (
        <div className="composer-media-loading">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Uploading…</span>
        </div>
      ) : src ? (
        <div className="composer-media-item">
          <img src={src} alt="Attached preview" />
          <button
            type="button"
            className="composer-media-remove"
            onClick={(event) => {
              event.stopPropagation();
              attachment.clear();
            }}
            aria-label="Remove image"
          >
            <X size={16} aria-hidden="true" />
          </button>
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
    if ((!comment.trim() && !media.mediaUrl) || media.uploading) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await createComment(tweet.id, comment.trim(), media.mediaUrl);
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
        to={`/profile/${encodeURIComponent(tweet.author.username)}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${tweet.author.username}`}
      >
        <Avatar user={tweet.author} />
      </Link>
      <div className="tweet-body">
        <header>
          <Link
            to={`/profile/${encodeURIComponent(tweet.author.username)}`}
            className="author-link"
            onClick={(event) => event.stopPropagation()}
          >
            <strong>@{tweet.author.username}</strong>
          </Link>
          <span>{displayDate}</span>
        </header>
        <p><RichContent text={tweet.content} /></p>
        {tweet.media_url ? <MediaAttachmentView url={tweet.media_url} /> : null}
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
              disabled={acting === "comment" || (!comment.trim() && !media.mediaUrl) || media.uploading}
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
    if ((!reply.trim() && !media.mediaUrl) || media.uploading) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await replyToComment(localComment.id, reply.trim(), media.mediaUrl);
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
    <article className={localComment.parent_comment_id ? "comment-card reply" : "comment-card"}>
      <Link
        to={`/profile/${encodeURIComponent(localComment.author.username)}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${localComment.author.username}`}
      >
        <Avatar user={localComment.author} size="small" />
      </Link>
      <div className="comment-body">
        <header>
          <Link
            to={`/profile/${encodeURIComponent(localComment.author.username)}`}
            className="author-link"
            onClick={(event) => event.stopPropagation()}
          >
            <strong>@{localComment.author.username}</strong>
          </Link>
          <span>{formatCompactDate(localComment.created_at)}</span>
          {localComment.parent_comment_id ? <span>Reply</span> : null}
        </header>
        <p><RichContent text={localComment.content} /></p>
        {localComment.media_url ? <MediaAttachmentView url={localComment.media_url} /> : null}
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
              disabled={acting === "comment" || (!reply.trim() && !media.mediaUrl) || media.uploading}
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
