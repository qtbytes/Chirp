import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";
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
  createComment,
  createTweet,
  followUser,
  getCommentStats,
  getCurrentUser,
  getTimeline,
  getTweet,
  getTweetStats,
  listComments,
  listUsers,
  login,
  logout,
  register,
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
import {
  Avatar,
  CommentCard,
  TweetCard,
  getErrorMessage,
  mergeCommentStats,
  mergeTweetStats,
  parseBackendDate,
} from "./components";
import { ProfileView } from "./ProfileView";

type AuthMode = "login" | "register";
type Theme = "light" | "dark";
type LayoutContext = { refreshToken: number; onDiscoveryChanged: () => void };

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
    <Routes>
      <Route
        element={
          <AppLayout
            currentUser={currentUser}
            onLogout={() => setCurrentUser(null)}
            theme={theme}
            onToggleTheme={toggleTheme}
          />
        }
      >
        <Route path="/" element={<HomeView />} />
        <Route path="/following" element={<HomeView />} />
        <Route path="/people" element={<PeopleRoute />} />
        <Route path="/tweet/:tweetId" element={<TweetDetailRoute />} />
        <Route
          path="/profile/:username"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route
          path="/profile/:username/replies"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
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

function AppLayout({
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
  const [refreshToken, setRefreshToken] = useState(0);
  const location = useLocation();
  const isPeopleRoute = location.pathname === "/people";
  const isHomeRoute = !isPeopleRoute && !location.pathname.startsWith("/profile");
  const onDiscoveryChanged = () => setRefreshToken((value) => value + 1);

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className={isPeopleRoute ? "app-shell app-shell--no-discovery" : "app-shell"}>
      <aside className="rail">
        <div className="rail-brand" aria-label="Twitter System">
          <Feather aria-hidden="true" />
        </div>
        <nav className="rail-nav" aria-label="Primary">
          <Link className={isHomeRoute ? "rail-link active" : "rail-link"} to="/">
            <Home size={22} aria-hidden="true" />
            <span>Home</span>
          </Link>
          <Link className={isPeopleRoute ? "rail-link active" : "rail-link"} to="/people">
            <Search size={22} aria-hidden="true" />
            <span>Search</span>
          </Link>
          <Link className={isPeopleRoute ? "rail-link active" : "rail-link"} to="/people">
            <Users size={22} aria-hidden="true" />
            <span>People</span>
          </Link>
          <button
            type="button"
            className="rail-link muted"
            disabled
            aria-disabled="true"
            title="Notifications aren't available yet"
          >
            <Bell size={22} aria-hidden="true" />
            <span>Updates</span>
          </button>
        </nav>
        <ThemeToggle theme={theme} onToggleTheme={onToggleTheme} />
        <div className="rail-user">
          <Link
            to={`/profile/${encodeURIComponent(currentUser.username)}`}
            className="author-link"
          >
            <Avatar user={currentUser} size="small" />
            <div>
              <strong>@{currentUser.username}</strong>
              <span>User {currentUser.id}</span>
            </div>
          </Link>
          <button className="icon-button" onClick={handleLogout} aria-label="Log out">
            <LogOut size={18} aria-hidden="true" />
          </button>
        </div>
      </aside>

      <main className="feed-column">
        <Outlet context={{ refreshToken, onDiscoveryChanged } satisfies LayoutContext} />
      </main>

      {isPeopleRoute ? null : (
        <aside className="discovery-column">
          <UserDiscoveryPanel onChanged={onDiscoveryChanged} />
        </aside>
      )}
    </div>
  );
}

function PeopleRoute() {
  const { onDiscoveryChanged } = useOutletContext<LayoutContext>();

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>People</h1>
        </div>
      </header>
      <UserDiscoveryPanel onChanged={onDiscoveryChanged} />
    </>
  );
}

function HomeView() {
  const { refreshToken } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const location = useLocation();
  const activeTab: TimelineKind = location.pathname === "/following" ? "following" : "for-you";
  const [page, setPage] = useState<TimelinePage | null>(null);
  const [tweetById, setTweetById] = useState<Record<number, Tweet>>({});
  const [tweetIds, setTweetIds] = useState<number[]>([]);
  const [loadingFeed, setLoadingFeed] = useState(false);
  const [feedError, setFeedError] = useState("");

  const tweets = useMemo(
    () => tweetIds.map((tweetId) => tweetById[tweetId]).filter((tweet): tweet is Tweet => Boolean(tweet)),
    [tweetById, tweetIds],
  );
  const visibleTweetIdsKey = useMemo(() => tweetIds.join(","), [tweetIds]);

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

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Home</h1>
        </div>
        <div className="tab-list" role="tablist" aria-label="Timeline">
          <button
            className={activeTab === "for-you" ? "tab active" : "tab"}
            onClick={() => navigate("/")}
            role="tab"
            aria-selected={activeTab === "for-you"}
          >
            For you
          </button>
          <button
            className={activeTab === "following" ? "tab active" : "tab"}
            onClick={() => navigate("/following")}
            role="tab"
            aria-selected={activeTab === "following"}
          >
            Following
          </button>
        </div>
      </header>

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
            onOpen={() => navigate(`/tweet/${tweet.id}`)}
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
  );
}

function TweetDetailRoute() {
  const { tweetId } = useParams();
  const navigate = useNavigate();
  const numericTweetId = Number(tweetId);
  const [tweet, setTweet] = useState<Tweet | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isInteger(numericTweetId) || numericTweetId <= 0) {
      setError("Tweet not found.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    getTweet(numericTweetId)
      .then((loaded) => {
        if (!cancelled) setTweet(loaded);
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [numericTweetId]);

  const patchTweet = useCallback((_tweetId: number, patch: Partial<Tweet>) => {
    setTweet((current) => (current ? { ...current, ...patch } : current));
  }, []);

  useEffect(() => {
    if (!tweet) return;
    const timer = window.setInterval(async () => {
      try {
        const stats = await getTweetStats([tweet.id]);
        if (stats[0]) {
          setTweet((current) =>
            current ? { ...current, ...stats[0], id: current.id } : current,
          );
        }
      } catch {
        // background sync; ignore failures
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [tweet?.id]);

  if (loading) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading</span>
      </div>
    );
  }
  if (error || !tweet) {
    return <div className="status-panel error">{error || "Tweet not found."}</div>;
  }
  return <TweetDetail tweet={tweet} onBack={() => navigate(-1)} onTweetPatch={patchTweet} />;
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
          <Link
            to={`/profile/${encodeURIComponent(tweet.author.username)}`}
            className="author-link"
            aria-label={`View profile of ${tweet.author.username}`}
          >
            <Avatar user={tweet.author} />
          </Link>
          <div>
            <Link
              to={`/profile/${encodeURIComponent(tweet.author.username)}`}
              className="author-link"
            >
              <strong>@{tweet.author.username}</strong>
            </Link>
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
            <Link
              to={`/profile/${encodeURIComponent(user.username)}`}
              className="author-link user-row-link"
              aria-label={`View profile of ${user.username}`}
            >
              <Avatar user={user} size="small" />
              <div className="user-copy">
                <strong>@{user.username}</strong>
                <span>{user.is_current_user ? "You" : `User ${user.id}`}</span>
              </div>
            </Link>
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

export default App;
