import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Heart, MessageCircle, Repeat2 } from "lucide-react";
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
    if (!comment.trim()) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await createComment(tweet.id, comment.trim());
      setComment("");
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
        <p>{tweet.content}</p>
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
            <input
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={1000}
              placeholder="Post your reply"
              aria-label="Comment"
            />
            <button className="primary-button compact" disabled={acting === "comment" || !comment.trim()}>
              Reply
            </button>
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
    if (!reply.trim()) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await replyToComment(localComment.id, reply.trim());
      setReply("");
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
        <p>{localComment.content}</p>
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
            <input
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              maxLength={1000}
              placeholder="Reply to this comment"
              aria-label="Reply to comment"
            />
            <button
              className="primary-button compact"
              disabled={acting === "comment" || !reply.trim()}
            >
              Reply
            </button>
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
