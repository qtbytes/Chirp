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
  getCurrentUser,
  getTimeline,
  likeComment,
  listComments,
  listUsers,
  login,
  logout,
  register,
  replyToComment,
  retweetComment,
  retweetTweet,
  toggleTweetLike,
  unfollowUser,
} from "./api";
import type {
  Comment,
  TimelineKind,
  TimelinePage,
  Tweet,
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
  const [tweets, setTweets] = useState<Tweet[]>([]);
  const [loadingFeed, setLoadingFeed] = useState(false);
  const [feedError, setFeedError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [selectedTweet, setSelectedTweet] = useState<Tweet | null>(null);

  const loadFeed = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");

      try {
        const nextPage = await getTimeline(activeTab, cursor);
        setPage(nextPage);
        setTweets((current) => (append ? [...current, ...nextPage.items] : nextPage.items));
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
                setSelectedTweet(null);
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
                setSelectedTweet(null);
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
            onBack={() => setSelectedTweet(null)}
            onChanged={() => setRefreshToken((value) => value + 1)}
          />
        ) : (
          <>
            <Composer onPosted={() => setRefreshToken((value) => value + 1)} />

            {feedError ? <div className="status-panel error">{feedError}</div> : null}
            {!loadingFeed && tweets.length === 0 && !feedError ? (
              <div className="status-panel">No tweets yet.</div>
            ) : null}
            <section className="tweet-list" aria-live="polite">
              {tweets.map((tweet) => (
                <TweetCard
                  key={tweet.id}
                  tweet={tweet}
                  onOpen={() => setSelectedTweet(tweet)}
                  onChanged={() => setRefreshToken((value) => value + 1)}
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

function Composer({ onPosted }: { onPosted: () => void }) {
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
      await createTweet(content.trim());
      setContent("");
      onPosted();
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
  onChanged,
}: {
  tweet: Tweet;
  onOpen: () => void;
  onChanged: () => void;
}) {
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [localTweet, setLocalTweet] = useState(tweet);
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");
  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(tweet.created_at));
  }, [tweet.created_at]);

  useEffect(() => {
    setLocalTweet(tweet);
  }, [tweet]);

  async function runRetweetAction(task: () => Promise<void>) {
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

  async function toggleLikeAction() {
    setActing("like");
    setError("");
    try {
      const result = await toggleTweetLike(localTweet.id);
      setLocalTweet((value) => ({
        ...value,
        liked_by_me: result.liked,
        like_count: result.like_count,
      }));
      onChanged();
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
      onChanged();
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
        {localTweet.author.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="tweet-body">
        <header>
          <strong>@{localTweet.author.username}</strong>
          <span>{displayDate}</span>
        </header>
        <p>{localTweet.content}</p>
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
            <span>{localTweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={(event) => {
              event.stopPropagation();
              void runRetweetAction(() => retweetTweet(localTweet.id));
            }}
            disabled={acting === "retweet"}
          >
            <Repeat2 size={18} aria-hidden="true" />
            <span>{localTweet.retweet_count}</span>
          </button>
          <button
            className={localTweet.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={(event) => {
              event.stopPropagation();
              void toggleLikeAction();
            }}
            disabled={acting === "like"}
            aria-pressed={localTweet.liked_by_me}
          >
            <Heart size={18} aria-hidden="true" fill={localTweet.liked_by_me ? "currentColor" : "none"} />
            <span>{localTweet.like_count}</span>
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
  onChanged,
}: {
  tweet: Tweet;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [comment, setComment] = useState("");
  const [currentTweet, setCurrentTweet] = useState(tweet);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");

  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(currentTweet.created_at));
  }, [currentTweet.created_at]);

  const loadTweetComments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setComments(await listComments(currentTweet.id));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [currentTweet.id]);

  useEffect(() => {
    setCurrentTweet(tweet);
  }, [tweet]);

  useEffect(() => {
    void loadTweetComments();
  }, [loadTweetComments]);

  async function runDetailRetweetAction(task: () => Promise<void>) {
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

  async function toggleDetailLikeAction() {
    setActing("like");
    setError("");
    try {
      const result = await toggleTweetLike(currentTweet.id);
      setCurrentTweet((value) => ({
        ...value,
        liked_by_me: result.liked,
        like_count: result.like_count,
      }));
      onChanged();
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
      await createComment(currentTweet.id, comment.trim());
      setComment("");
      setCurrentTweet((value) => ({
        ...value,
        comment_count: value.comment_count + 1,
      }));
      await loadTweetComments();
      onChanged();
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
            {currentTweet.author.username.slice(0, 1).toUpperCase()}
          </div>
          <div>
            <strong>@{currentTweet.author.username}</strong>
            <span>{displayDate}</span>
          </div>
        </div>
        <p>{currentTweet.content}</p>
        {error ? <p className="tweet-error">{error}</p> : null}
        <div className="tweet-actions detail-actions">
          <button
            className="tweet-action comment"
            onClick={() => document.getElementById("detail-comment-input")?.focus()}
          >
            <MessageCircle size={18} aria-hidden="true" />
            <span>{currentTweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={() => void runDetailRetweetAction(() => retweetTweet(currentTweet.id))}
            disabled={acting === "retweet"}
          >
            <Repeat2 size={18} aria-hidden="true" />
            <span>{currentTweet.retweet_count}</span>
          </button>
          <button
            className={currentTweet.liked_by_me ? "tweet-action like active" : "tweet-action like"}
            onClick={() => void toggleDetailLikeAction()}
            disabled={acting === "like"}
            aria-pressed={currentTweet.liked_by_me}
          >
            <Heart
              size={18}
              aria-hidden="true"
              fill={currentTweet.liked_by_me ? "currentColor" : "none"}
            />
            <span>{currentTweet.like_count}</span>
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
              onChanged();
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
}: {
  comment: Comment;
  onChanged: () => void;
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState("");
  const [localComment, setLocalComment] = useState(comment);
  const [acting, setActing] = useState<"like" | "retweet" | "comment" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setLocalComment(comment);
  }, [comment]);

  async function runCommentAction(action: "like" | "retweet", task: () => Promise<void>) {
    setActing(action);
    setError("");
    try {
      await task();
      setLocalComment((value) => ({
        ...value,
        like_count: action === "like" ? value.like_count + 1 : value.like_count,
        retweet_count: action === "retweet" ? value.retweet_count + 1 : value.retweet_count,
      }));
      onChanged();
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
              void runCommentAction("retweet", () => retweetComment(localComment.id))
            }
            disabled={acting === "retweet"}
          >
            <Repeat2 size={16} aria-hidden="true" />
            <span>{localComment.retweet_count}</span>
          </button>
          <button
            className="tweet-action like"
            onClick={() => void runCommentAction("like", () => likeComment(localComment.id))}
            disabled={acting === "like"}
          >
            <Heart size={16} aria-hidden="true" />
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
  }).format(new Date(value));
}

export default App;
