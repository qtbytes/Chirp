import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Bell,
  Feather,
  Home,
  Heart,
  Loader2,
  LogOut,
  Moon,
  MessageCircle,
  Repeat2,
  Search,
  Sun,
  UserPlus,
  Users,
} from "lucide-react";
import {
  ApiError,
  createComment,
  createTweet,
  followUser,
  getCommentStats,
  getCurrentUser,
  getTimeline,
  getTweetStats,
  listComments,
  listUsers,
  login,
  logout,
  register,
  replyToComment,
  retweetComment,
  retweetTweet,
  toggleCommentLike,
  toggleTweetLike,
  unfollowUser,
} from "./api";
import type {
  Comment,
  CommentStats,
  TimelineKind,
  TimelinePage,
  Tweet,
  TweetStats,
  UserDiscovery,
  UserSummary,
} from "./types";

type AuthMode = "login" | "register";
type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "twitter-system-theme";

function getSystemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function App() {
  const [currentUser, setCurrentUser] = useState<UserSummary | null>(null);
  const [booting, setBooting] = useState(true);
  const [theme, setTheme] = useState<Theme>(() => {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    return savedTheme === "light" || savedTheme === "dark" ? savedTheme : getSystemTheme();
  });

  useEffect(() => {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (savedTheme === "light" || savedTheme === "dark") {
      return;
    }

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const syncSystemTheme = () => {
      const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (savedTheme !== "light" && savedTheme !== "dark") {
        setTheme(media.matches ? "dark" : "light");
      }
    };
    media.addEventListener("change", syncSystemTheme);
    return () => media.removeEventListener("change", syncSystemTheme);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  function toggleTheme() {
    setTheme((currentTheme) => {
      const nextTheme = currentTheme === "dark" ? "light" : "dark";
      window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
      return nextTheme;
    });
  }

  useEffect(() => {
    getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null))
      .finally(() => setBooting(false));
  }, []);

  if (booting) {
    return (
      <main className="boot-screen">
        <Loader2 className="spin" aria-hidden="true" />
      </main>
    );
  }

  if (!currentUser) {
    return (
      <AuthScreen
        onAuthenticated={setCurrentUser}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    );
  }

  return (
    <MainApp
      currentUser={currentUser}
      onLogout={() => setCurrentUser(null)}
      theme={theme}
      onToggleTheme={toggleTheme}
    />
  );
}

function AuthScreen({
  onAuthenticated,
  theme,
  onToggleTheme,
}: {
  onAuthenticated: (user: UserSummary) => void;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      const user =
        mode === "login"
          ? await login(username.trim(), password)
          : await register(username.trim(), password);
      onAuthenticated(user);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <ThemeToggle theme={theme} onToggleTheme={onToggleTheme} className="auth-theme-toggle" />
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="brand-mark">
          <Feather size={30} aria-hidden="true" />
        </div>
        <h1 id="auth-title">Twitter System</h1>
        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            <span>Username</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              minLength={3}
              maxLength={50}
              autoComplete="username"
              required
            />
          </label>
          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={8}
              maxLength={128}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
            />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
          <button className="primary-button" disabled={submitting}>
            {submitting ? "Working..." : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        <button
          className="text-button"
          onClick={() => {
            setError("");
            setMode(mode === "login" ? "register" : "login");
          }}
        >
          {mode === "login" ? "Create account" : "Use existing account"}
        </button>
      </section>
    </main>
  );
}

function MainApp({
  currentUser,
  onLogout,
  theme,
  onToggleTheme,
}: {
  currentUser: UserSummary;
  onLogout: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TimelineKind>("for-you");
  const [page, setPage] = useState<TimelinePage | null>(null);
  const [tweetById, setTweetById] = useState<Record<number, Tweet>>({});
  const [tweetIds, setTweetIds] = useState<number[]>([]);
  const [loadingFeed, setLoadingFeed] = useState(false);
  const [feedError, setFeedError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [selectedTweetId, setSelectedTweetId] = useState<number | null>(null);

  const tweets = useMemo(
    () => tweetIds.map((tweetId) => tweetById[tweetId]).filter((tweet): tweet is Tweet => Boolean(tweet)),
    [tweetById, tweetIds],
  );
  const selectedTweet = selectedTweetId === null ? null : tweetById[selectedTweetId] ?? null;
  const visibleTweetIdsKey = useMemo(() => {
    const visibleIds = selectedTweetId === null ? tweetIds : [...tweetIds, selectedTweetId];
    return Array.from(new Set(visibleIds)).join(",");
  }, [selectedTweetId, tweetIds]);

  const patchTweet = useCallback((tweetId: number, patch: Partial<Tweet>) => {
    setTweetById((current) => {
      const existing = current[tweetId];
      if (!existing) {
        return current;
      }
      return {
        ...current,
        [tweetId]: {
          ...existing,
          ...patch,
        },
      };
    });
  }, []);

  const loadFeed = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");

      try {
        const nextPage = await getTimeline(activeTab, cursor);
        setPage(nextPage);
        setTweetById((current) => {
          const next = { ...current };
          for (const tweet of nextPage.items) {
            next[tweet.id] = tweet;
          }
          return next;
        });
        setTweetIds((current) => {
          const nextIds = nextPage.items.map((tweet) => tweet.id);
          if (!append) {
            return nextIds;
          }
          const existing = new Set(current);
          return [...current, ...nextIds.filter((tweetId) => !existing.has(tweetId))];
        });
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [activeTab],
  );

  useEffect(() => {
    void loadFeed();
  }, [loadFeed, refreshToken]);

  useEffect(() => {
    if (!visibleTweetIdsKey) {
      return;
    }

    const tweetIdsToSync = visibleTweetIdsKey.split(",").map(Number);
    let cancelled = false;

    async function syncTweetStats() {
      try {
        const stats = await getTweetStats(tweetIdsToSync);
        if (cancelled) {
          return;
        }
        setTweetById((current) => mergeTweetStats(current, stats));
      } catch {
        // Stats polling is a background sync; keep the current UI if it fails.
      }
    }

    void syncTweetStats();
    const timer = window.setInterval(() => void syncTweetStats(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [visibleTweetIdsKey]);

  function insertPostedTweet(tweet: Tweet) {
    setTweetById((current) => ({
      ...current,
      [tweet.id]: tweet,
    }));
    setTweetIds((current) => [tweet.id, ...current.filter((tweetId) => tweetId !== tweet.id)]);
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="rail-brand" aria-label="Twitter System">
          <Feather aria-hidden="true" />
        </div>
        <nav className="rail-nav" aria-label="Primary">
          <a className="rail-link active" href="#feed">
            <Home size={22} aria-hidden="true" />
            <span>Home</span>
          </a>
          <a className="rail-link" href="#discover">
            <Search size={22} aria-hidden="true" />
            <span>Search</span>
          </a>
          <a className="rail-link" href="#discover">
            <Users size={22} aria-hidden="true" />
            <span>People</span>
          </a>
          <a className="rail-link muted" href="#feed">
            <Bell size={22} aria-hidden="true" />
            <span>Updates</span>
          </a>
        </nav>
        <ThemeToggle theme={theme} onToggleTheme={onToggleTheme} />
        <div className="rail-user">
          <div>
            <strong>@{currentUser.username}</strong>
            <span>User {currentUser.id}</span>
          </div>
          <button className="icon-button" onClick={handleLogout} aria-label="Log out">
            <LogOut size={18} aria-hidden="true" />
          </button>
        </div>
      </aside>

      <main id="feed" className="feed-column">
        <header className="feed-header">
          <div className="feed-title-row">
            <h1>Home</h1>
            <ThemeToggle
              theme={theme}
              onToggleTheme={onToggleTheme}
              className="feed-theme-toggle"
            />
          </div>
          <div className="tab-list" role="tablist" aria-label="Timeline">
            <button
              className={activeTab === "for-you" ? "tab active" : "tab"}
              onClick={() => {
                setSelectedTweetId(null);
                setActiveTab("for-you");
              }}
              role="tab"
              aria-selected={activeTab === "for-you"}
            >
              For you
            </button>
            <button
              className={activeTab === "following" ? "tab active" : "tab"}
              onClick={() => {
                setSelectedTweetId(null);
                setActiveTab("following");
              }}
              role="tab"
              aria-selected={activeTab === "following"}
            >
              Following
            </button>
          </div>
        </header>

        {selectedTweet ? (
          <TweetDetail
            tweet={selectedTweet}
            onBack={() => setSelectedTweetId(null)}
            onTweetPatch={patchTweet}
          />
        ) : (
          <>
            <Composer onPosted={insertPostedTweet} />

            {feedError ? <div className="status-panel error">{feedError}</div> : null}
            {!loadingFeed && tweets.length === 0 && !feedError ? (
              <div className="status-panel">No tweets yet.</div>
            ) : null}
            <section className="tweet-list" aria-live="polite">
              {tweets.map((tweet) => (
                <TweetCard
                  key={tweet.id}
                  tweet={tweet}
                  onOpen={() => setSelectedTweetId(tweet.id)}
                  onTweetPatch={patchTweet}
                />
              ))}
            </section>
            {loadingFeed ? (
              <div className="loading-row">
                <Loader2 className="spin" size={18} aria-hidden="true" />
                <span>Loading</span>
              </div>
            ) : null}
            {page?.next_cursor ? (
              <button
                className="load-more"
                onClick={() => void loadFeed(page.next_cursor, true)}
                disabled={loadingFeed}
              >
                Load more
              </button>
            ) : null}
          </>
        )}
      </main>

      <aside id="discover" className="discovery-column">
        <UserDiscoveryPanel onChanged={() => setRefreshToken((value) => value + 1)} />
      </aside>
    </div>
  );
}

function ThemeToggle({
  theme,
  onToggleTheme,
  className = "",
}: {
  theme: Theme;
  onToggleTheme: () => void;
  className?: string;
}) {
  const Icon = theme === "dark" ? Sun : Moon;
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <button
      className={`theme-toggle ${className}`.trim()}
      onClick={onToggleTheme}
      aria-label={`Use ${nextTheme} mode`}
      title={`Use ${nextTheme} mode`}
    >
      <Icon size={19} aria-hidden="true" />
      <span>{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}

function Composer({ onPosted }: { onPosted: (tweet: Tweet) => void }) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [posting, setPosting] = useState(false);
  const remaining = 280 - content.length;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!content.trim() || remaining < 0) {
      return;
    }

    setPosting(true);
    setError("");
    try {
      const tweet = await createTweet(content.trim());
      setContent("");
      onPosted(tweet);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setPosting(false);
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <textarea
        value={content}
        onChange={(event) => setContent(event.target.value)}
        maxLength={280}
        placeholder="What is happening?"
        aria-label="Tweet content"
      />
      {error ? <p className="form-error">{error}</p> : null}
      <div className="composer-actions">
        <span className={remaining < 30 ? "counter warn" : "counter"}>{remaining}</span>
        <button className="primary-button compact" disabled={posting || !content.trim()}>
          {posting ? "Posting..." : "Post"}
        </button>
      </div>
    </form>
  );
}

function TweetCard({
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
      <div className="avatar" aria-hidden="true">
        {tweet.author.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="tweet-body">
        <header>
          <strong>@{tweet.author.username}</strong>
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

function TweetDetail({
  tweet,
  onBack,
  onTweetPatch,
}: {
  tweet: Tweet;
  onBack: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
}) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");
  const commentIdsKey = useMemo(
    () => comments.map((item) => item.id).join(","),
    [comments],
  );

  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(parseBackendDate(tweet.created_at));
  }, [tweet.created_at]);

  const loadTweetComments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setComments(await listComments(tweet.id));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [tweet.id]);

  useEffect(() => {
    void loadTweetComments();
  }, [loadTweetComments]);

  useEffect(() => {
    if (!commentIdsKey) {
      return;
    }

    const commentIdsToSync = commentIdsKey.split(",").map(Number);
    let cancelled = false;

    async function syncCommentStats() {
      try {
        const stats = await getCommentStats(commentIdsToSync);
        if (cancelled) {
          return;
        }
        setComments((current) => mergeCommentStats(current, stats));
      } catch {
        // Keep comment stats polling quiet; the full comment loader handles visible errors.
      }
    }

    const timer = window.setInterval(() => void syncCommentStats(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [commentIdsKey]);

  async function runDetailRetweetAction() {
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

  async function toggleDetailLikeAction() {
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

  async function submitDetailComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!comment.trim()) {
      return;
    }

    setActing("comment");
    setError("");
    try {
      await createComment(tweet.id, comment.trim());
      setComment("");
      onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
      await loadTweetComments();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActing(null);
    }
  }

  return (
    <section className="tweet-detail" aria-labelledby="tweet-detail-title">
      <div className="detail-toolbar">
        <button className="icon-button" onClick={onBack} aria-label="Back to timeline">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <h2 id="tweet-detail-title">Tweet</h2>
      </div>

      <article className="detail-tweet">
        <div className="detail-author">
          <div className="avatar" aria-hidden="true">
            {tweet.author.username.slice(0, 1).toUpperCase()}
          </div>
          <div>
            <strong>@{tweet.author.username}</strong>
            <span>{displayDate}</span>
          </div>
        </div>
        <p>{tweet.content}</p>
        {error ? <p className="tweet-error">{error}</p> : null}
        <div className="tweet-actions detail-actions">
          <button
            className="tweet-action comment"
            onClick={() => document.getElementById("detail-comment-input")?.focus()}
          >
            <MessageCircle size={18} aria-hidden="true" />
            <span>{tweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={() => void runDetailRetweetAction()}
            disabled={acting === "retweet"}
          >
            <Repeat2 size={18} aria-hidden="true" />
            <span>{tweet.retweet_count}</span>
          </button>
          <button
            className={tweet.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={() => void toggleDetailLikeAction()}
            disabled={acting === "like"}
            aria-pressed={tweet.liked_by_me}
          >
            <Heart
              size={18}
              aria-hidden="true"
              fill={tweet.liked_by_me ? "currentColor" : "none"}
            />
            <span>{tweet.like_count}</span>
          </button>
        </div>
        <form className="comment-form detail-comment-form" onSubmit={submitDetailComment}>
          <input
            id="detail-comment-input"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={1000}
            placeholder="Post your reply"
            aria-label="Comment"
          />
          <button
            className="primary-button compact"
            disabled={acting === "comment" || !comment.trim()}
          >
            Reply
          </button>
        </form>
      </article>

      <section className="comment-list" aria-label="Comments">
        {loading ? (
          <div className="loading-row">
            <Loader2 className="spin" size={18} aria-hidden="true" />
            <span>Loading comments</span>
          </div>
        ) : null}
        {!loading && comments.length === 0 ? (
          <div className="status-panel">No comments yet.</div>
        ) : null}
        {comments.map((item) => (
          <CommentCard
            key={item.id}
            comment={item}
            onChanged={() => {
              void loadTweetComments();
            }}
            onReplyCreated={() => {
              onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
            }}
          />
        ))}
      </section>
    </section>
  );
}

function CommentCard({
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
      <div className="avatar small" aria-hidden="true">
        {localComment.author.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="comment-body">
        <header>
          <strong>@{localComment.author.username}</strong>
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

function UserDiscoveryPanel({ onChanged }: { onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<UserDiscovery[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setUsers(await listUsers(query));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadUsers();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [loadUsers]);

  async function toggleFollow(user: UserDiscovery) {
    try {
      if (user.is_following) {
        await unfollowUser(user.id);
      } else {
        await followUser(user.id);
      }
      await loadUsers();
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  return (
    <section className="discovery-panel" aria-labelledby="discover-title">
      <h2 id="discover-title">People</h2>
      <label className="search-box">
        <Search size={18} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search users"
          aria-label="Search users"
        />
      </label>
      {error ? <p className="form-error">{error}</p> : null}
      <div className="user-list">
        {users.map((user) => (
          <div className="user-row" key={user.id}>
            <div className="avatar small" aria-hidden="true">
              {user.username.slice(0, 1).toUpperCase()}
            </div>
            <div className="user-copy">
              <strong>@{user.username}</strong>
              <span>{user.is_current_user ? "You" : `User ${user.id}`}</span>
            </div>
            {!user.is_current_user ? (
              <button className="follow-button" onClick={() => void toggleFollow(user)}>
                <UserPlus size={15} aria-hidden="true" />
                {user.is_following ? "Following" : "Follow"}
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {loading ? (
        <div className="loading-row small">
          <Loader2 className="spin" size={16} aria-hidden="true" />
        </div>
      ) : null}
    </section>
  );
}

function mergeTweetStats(
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

function mergeCommentStats(current: Comment[], stats: CommentStats[]): Comment[] {
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

function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) {
    return err.message;
  }
  return "Something went wrong.";
}

function formatCompactDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parseBackendDate(value));
}

function parseBackendDate(value: string): Date {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

export default App;
