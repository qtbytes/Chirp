import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  AtSign,
  BarChart2,
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
  Settings,
  Sun,
  UserPlus,
  X,
} from "lucide-react";
import {
  createComment,
  createTweet,
  deleteTweet,
  displayName,
  editTweet,
  followUser,
  getCommentStats,
  getCurrentUser,
  getTimeline,
  getTweet,
  getTweetStats,
  recordPostViews,
  getUnreadNotificationCount,
  listComments,
  listNotifications,
  listUsers,
  login,
  logout,
  markNotificationRead,
  getHashtagPosts,
  getTrending,
  markNotificationsRead,
  notificationStreamUrl,
  register,
  searchPosts,
  toggleTweetLike,
  unfollowUser,
} from "./api";
import type {
  Comment,
  Notification,
  ProfileTweetsPage,
  SearchPost,
  SearchSort,
  TimelineKind,
  TimelinePage,
  TrendingHashtag,
  Tweet,
  TweetVisibility,
  UserDiscovery,
  UserSummary,
} from "./types";
import {
  Avatar,
  CommentCard,
  ConfirmDialog,
  CurrentUserProvider,
  MediaButton,
  MediaGallery,
  MediaPreview,
  PostEditor,
  PostMenu,
  PostBody,
  QuoteComposer,
  QuotedPostCard,
  ReplyComposer,
  TweetCard,
  VisibilityBadge,
  VisibilityPicker,
  formatCompactDate,
  getErrorMessage,
  mergeCommentStats,
  mergeTweetStats,
  parseBackendDate,
} from "./components";
import { ProfileView } from "./ProfileView";
import { FollowListView } from "./FollowListView";
import { AccountView } from "./AccountView";
import { ResetPasswordView, VerifyEmailView } from "./AuthTokenViews";
import { ForgotPasswordModal } from "./ForgotPasswordModal";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import { useMediaAttachment } from "./useMediaAttachment";

type AuthMode = "login" | "register";
type Theme = "light" | "dark";
type LayoutContext = {
  currentUser: UserSummary;
  refreshToken: number;
  onDiscoveryChanged: () => void;
  refreshUnread: () => void;
};

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

  // Reachable signed out: a reset link is opened by someone who cannot log in,
  // and a confirmation link often lands in a browser that never has.
  const tokenRoutes = (
    <>
      <Route path="/reset-password" element={<ResetPasswordView />} />
      <Route path="/verify-email" element={<VerifyEmailView />} />
    </>
  );

  if (!currentUser) {
    return (
      <Routes>
        {tokenRoutes}
        <Route
          path="*"
          element={
            <AuthScreen
              onAuthenticated={setCurrentUser}
              theme={theme}
              onToggleTheme={toggleTheme}
            />
          }
        />
      </Routes>
    );
  }

  return (
    <CurrentUserProvider value={currentUser}>
    <Routes>
      {/* Also here: a signed-in user clicking their own confirmation link must
          land on the page, not on /:username. */}
      {tokenRoutes}
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
        <Route path="/search" element={<SearchView />} />
        <Route path="/hashtag/:tag" element={<HashtagView />} />
        <Route path="/notifications" element={<NotificationsView />} />
        <Route
          path="/settings"
          element={
            <AccountView
              currentUser={currentUser}
              onLoggedOut={() => setCurrentUser(null)}
            />
          }
        />
        <Route path="/tweet/:tweetId" element={<TweetDetailRoute />} />
        <Route
          path="/:username"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route
          path="/:username/replies"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route path="/:username/followers" element={<FollowListView />} />
        <Route path="/:username/following" element={<FollowListView />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </CurrentUserProvider>
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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      const user =
        mode === "login"
          ? await login(username.trim(), password)
          : await register(username.trim(), email.trim(), password);
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
        <h1 id="auth-title">Chirp</h1>
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
          {mode === "register" ? (
            <label>
              <span>Email</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                maxLength={254}
                autoComplete="email"
                required
              />
            </label>
          ) : null}
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
          {mode === "register" ? (
            <p className="form-hint">
              We'll send a link to confirm it. Without a confirmed address you
              cannot reset a forgotten password.
            </p>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
          <button className="primary-button" disabled={submitting}>
            {submitting ? "Working..." : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        {mode === "login" ? (
          <button className="text-button" onClick={() => setForgotOpen(true)}>
            Forgot password?
          </button>
        ) : null}
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
      {forgotOpen ? <ForgotPasswordModal onClose={() => setForgotOpen(false)} /> : null}
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
  const [unread, setUnread] = useState(0);
  const location = useLocation();
  const isSearchRoute = location.pathname === "/search";
  const isNotificationsRoute = location.pathname === "/notifications";
  const isSettingsRoute = location.pathname === "/settings";
  const isHomeRoute =
    location.pathname === "/" || location.pathname === "/following";
  const hideDiscovery = isSearchRoute || isNotificationsRoute || isSettingsRoute;
  const onDiscoveryChanged = () => setRefreshToken((value) => value + 1);

  const refreshUnread = useCallback(async () => {
    try {
      const { count } = await getUnreadNotificationCount();
      setUnread(count);
    } catch {
      // Ignore polling failures; the badge just keeps its last value.
    }
  }, []);

  useEffect(() => {
    void refreshUnread();
    // A slow poll as the safety net: the SSE stream below makes the badge live,
    // but a dropped connection or a Redis-less backend still recovers here.
    const timer = window.setInterval(() => void refreshUnread(), 30000);
    return () => window.clearInterval(timer);
  }, [refreshUnread]);

  // Live updates: an SSE nudge means "something changed", so re-read the
  // authoritative count rather than trust the event to carry it. The browser
  // reconnects an EventSource on its own; if the stream is unavailable (no
  // Redis -> 503) it simply stays closed and the poll above covers the gap.
  useEffect(() => {
    let source: EventSource | null = null;
    try {
      source = new EventSource(notificationStreamUrl(), { withCredentials: true });
      source.addEventListener("notification", () => void refreshUnread());
    } catch {
      source = null;
    }
    return () => source?.close();
  }, [refreshUnread]);

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className={hideDiscovery ? "app-shell app-shell--no-discovery" : "app-shell"}>
      <aside className="rail">
        <Link className="rail-brand" to="/" aria-label="Chirp home">
          <Feather aria-hidden="true" />
        </Link>
        <nav className="rail-nav" aria-label="Primary">
          <Link className={isHomeRoute ? "rail-link active" : "rail-link"} to="/">
            <Home size={22} aria-hidden="true" />
            <span>Home</span>
          </Link>
          <Link className={isSearchRoute ? "rail-link active" : "rail-link"} to="/search">
            <Search size={22} aria-hidden="true" />
            <span>Search</span>
          </Link>
          <Link
            className={isNotificationsRoute ? "rail-link active" : "rail-link"}
            to="/notifications"
            aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
          >
            <span className="rail-icon">
              <Bell size={22} aria-hidden="true" />
              {unread > 0 ? (
                <span className="rail-badge" aria-hidden="true">
                  {unread > 99 ? "99+" : unread}
                </span>
              ) : null}
            </span>
            <span>Notifications</span>
          </Link>
          <Link
            className={isSettingsRoute ? "rail-link active" : "rail-link"}
            to="/settings"
          >
            <Settings size={22} aria-hidden="true" />
            <span>Settings</span>
          </Link>
        </nav>
        <div className="rail-user">
          <Link
            to={`/${encodeURIComponent(currentUser.username)}`}
            className="author-link"
          >
            <Avatar user={currentUser} size="small" />
            <div>
              <strong>{displayName(currentUser)}</strong>
              <span>@{currentUser.username}</span>
            </div>
          </Link>
          <div className="rail-user-actions">
            <ThemeToggle theme={theme} onToggleTheme={onToggleTheme} iconOnly />
            <button className="icon-button" onClick={handleLogout} aria-label="Log out">
              <LogOut size={18} aria-hidden="true" />
            </button>
          </div>
        </div>
      </aside>

      <main className="feed-column">
        <Outlet
          context={{ currentUser, refreshToken, onDiscoveryChanged, refreshUnread } satisfies LayoutContext}
        />
      </main>

      {hideDiscovery ? null : (
        <aside className="discovery-column">
          <SidebarSearchBar />
          <TrendingPanel />
          <UserDiscoveryPanel onChanged={onDiscoveryChanged} hideSearch />
        </aside>
      )}
    </div>
  );
}

type SearchTab = "top" | "latest" | "people";

const SEARCH_HISTORY_KEY = "chirp-search-history";
const SEARCH_HISTORY_MAX = 20;

function getSearchHistory(): string[] {
  try {
    const raw = window.localStorage.getItem(SEARCH_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function addSearchHistory(term: string) {
  const history = getSearchHistory().filter((item) => item !== term);
  history.unshift(term);
  if (history.length > SEARCH_HISTORY_MAX) history.length = SEARCH_HISTORY_MAX;
  window.localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(history));
}

function removeSearchHistory(term: string) {
  const history = getSearchHistory().filter((item) => item !== term);
  window.localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(history));
}

function clearSearchHistory() {
  window.localStorage.removeItem(SEARCH_HISTORY_KEY);
}

function SearchHistoryPanel({
  onSelect,
  onChanged,
}: {
  onSelect: (term: string) => void;
  onChanged: () => void;
}) {
  const history = getSearchHistory();

  if (history.length === 0) return null;

  return (
    <section className="search-history" aria-label="Recent searches">
      <div className="search-history-header">
        <h2>Recent</h2>
        <button
          className="text-button"
          onClick={() => {
            clearSearchHistory();
            onChanged();
          }}
        >
          Clear all
        </button>
      </div>
      <ul className="search-history-list">
        {history.map((term) => (
          <li key={term} className="search-history-item">
            <button
              className="search-history-link"
              onClick={() => onSelect(term)}
            >
              <Search size={18} aria-hidden="true" />
              <span>{term}</span>
            </button>
            <button
              className="search-history-remove"
              onClick={() => {
                removeSearchHistory(term);
                onChanged();
              }}
              aria-label={`Remove ${term}`}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SearchView() {
  const { currentUser, onDiscoveryChanged } = useOutletContext<LayoutContext>();
  const [searchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [tab, setTab] = useState<SearchTab>("top");
  const [historyKey, setHistoryKey] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeQuery = query.trim();

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  // Save to history when a search is actually executed (debounce fires)
  const committedQuery = useRef("");
  useEffect(() => {
    if (!activeQuery) return;
    const timer = window.setTimeout(() => {
      if (activeQuery && activeQuery !== committedQuery.current) {
        committedQuery.current = activeQuery;
        addSearchHistory(activeQuery);
        setHistoryKey((k) => k + 1);
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [activeQuery]);

  // Close history dropdown when clicking outside
  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setShowHistory(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function selectHistoryItem(term: string) {
    setQuery(term);
    setShowHistory(false);
    inputRef.current?.focus();
  }

  const showHistoryPanel = showHistory && !activeQuery;

  return (
    <>
      <header className="feed-header search-feed-header">
        <div className="feed-title-row search-title-row">
          <h1>Search</h1>
        </div>
        <div className="search-header-bar" ref={searchRef}>
          <label className="search-box search-box--header">
            <Search size={18} aria-hidden="true" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onFocus={() => setShowHistory(true)}
              placeholder="Search"
              aria-label="Search"
            />
          </label>
          {showHistoryPanel ? (
            <SearchHistoryPanel
              key={historyKey}
              onSelect={selectHistoryItem}
              onChanged={() => setHistoryKey((k) => k + 1)}
            />
          ) : null}
        </div>
        {activeQuery ? (
          <div className="tab-list tab-list--three" role="tablist" aria-label="Search results">
            <button
              className={tab === "top" ? "tab active" : "tab"}
              onClick={() => setTab("top")}
              role="tab"
              aria-selected={tab === "top"}
            >
              Top
            </button>
            <button
              className={tab === "latest" ? "tab active" : "tab"}
              onClick={() => setTab("latest")}
              role="tab"
              aria-selected={tab === "latest"}
            >
              Latest
            </button>
            <button
              className={tab === "people" ? "tab active" : "tab"}
              onClick={() => setTab("people")}
              role="tab"
              aria-selected={tab === "people"}
            >
              People
            </button>
          </div>
        ) : null}
      </header>
      {activeQuery ? (
        tab === "people" ? (
          <UserDiscoveryPanel onChanged={onDiscoveryChanged} hideHeading hideSearch initialQuery={activeQuery} />
        ) : (
          <SearchPostsPanel
            currentUser={currentUser}
            query={activeQuery}
            sort={tab === "latest" ? "recent" : "relevance"}
          />
        )
      ) : null}
    </>
  );
}

function SearchReplyCard({
  post,
  onOpen,
}: {
  post: SearchPost;
  onOpen: () => void;
}) {
  return (
    <article className="search-reply" onClick={onOpen}>
      <div className="search-reply-head">
        <Avatar user={post.author} size="small" />
        <Link
          to={`/${encodeURIComponent(post.author.username)}`}
          className="author-link"
          onClick={(event) => event.stopPropagation()}
        >
          <strong>{displayName(post.author)}</strong>
          <span>@{post.author.username}</span>
        </Link>
        <span className="search-reply-meta">
          · reply · {formatCompactDate(post.created_at)}
        </span>
      </div>
      <PostBody text={post.content} enablePreview={false} />
    </article>
  );
}

function SearchPostsPanel({
  currentUser,
  query,
  sort,
}: {
  currentUser: UserSummary;
  query: string;
  sort: SearchSort;
}) {
  const navigate = useNavigate();
  const [postById, setPostById] = useState<Record<number, SearchPost>>({});
  const [ids, setIds] = useState<number[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);

  const posts = useMemo(
    () => ids.map((id) => postById[id]).filter((post): post is SearchPost => Boolean(post)),
    [ids, postById],
  );

  const patchPost = useCallback((postId: number, patch: Partial<Tweet>) => {
    setPostById((current) => {
      const existing = current[postId];
      if (!existing) {
        return current;
      }
      return { ...current, [postId]: { ...existing, ...patch } };
    });
  }, []);

  const removePost = useCallback((postId: number) => {
    setIds((current) => current.filter((id) => id !== postId));
    setPostById((current) => {
      const next = { ...current };
      delete next[postId];
      return next;
    });
  }, []);

  const removeByAuthor = useCallback(
    (authorId: number) => {
      setPostById((current) => {
        const removed = new Set(
          Object.values(current)
            .filter((post) => post.author.id === authorId)
            .map((post) => post.id),
        );
        setIds((currentIds) => currentIds.filter((id) => !removed.has(id)));
        const next = { ...current };
        removed.forEach((id) => delete next[id]);
        return next;
      });
    },
    [],
  );

  const runSearch = useCallback(
    async (term: string, nextCursor?: string | null, append = false) => {
      setLoading(true);
      setError("");
      try {
        const page = await searchPosts(term, nextCursor, sort);
        setPostById((current) => {
          const next = append ? { ...current } : {};
          for (const post of page.items) {
            next[post.id] = post;
          }
          return next;
        });
        setIds((current) => {
          const nextIds = page.items.map((post) => post.id);
          if (!append) {
            return nextIds;
          }
          const existing = new Set(current);
          return [...current, ...nextIds.filter((id) => !existing.has(id))];
        });
        setCursor(page.next_cursor);
        void recordPostViews(page.items.map((post) => post.id));
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [sort],
  );

  useEffect(() => {
    setIds([]);
    setPostById({});
    setCursor(null);
    setSearched(false);
    const timer = window.setTimeout(() => {
      setSearched(true);
      void runSearch(query);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, runSearch]);

  return (
    <section className="search-results-panel" aria-label="Search posts">
      {error ? <p className="form-error">{error}</p> : null}
      {searched && !loading && posts.length === 0 && !error ? (
        <div className="status-panel">No posts found.</div>
      ) : null}
      <section className="tweet-list" aria-live="polite">
        {posts.map((post) =>
          post.is_reply ? (
            <SearchReplyCard
              key={post.id}
              post={post}
              onOpen={() =>
                navigate(`/tweet/${post.thread_id}`, {
                  state: { scrollToPostId: post.id },
                })
              }
            />
          ) : (
            <TweetCard
              key={post.id}
              tweet={post}
              onOpen={() => navigate(`/tweet/${post.id}`)}
              onTweetPatch={patchPost}
              currentUserId={currentUser.id}
              onDeleted={removePost}
              onAuthorMuted={removeByAuthor}
              onAuthorBlocked={removeByAuthor}
            />
          ),
        )}
      </section>
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Searching</span>
        </div>
      ) : null}
      {cursor ? (
        <button
          className="load-more"
          onClick={() => void runSearch(query, cursor, true)}
          disabled={loading}
        >
          Load more
        </button>
      ) : null}
    </section>
  );
}

function HashtagView() {
  const { tag } = useParams();
  const navigate = useNavigate();
  const { currentUser } = useOutletContext<LayoutContext>();
  const [page, setPage] = useState<ProfileTweetsPage | null>(null);
  const [tweetById, setTweetById] = useState<Record<number, Tweet>>({});
  const [tweetIds, setTweetIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const tweets = useMemo(
    () => tweetIds.map((id) => tweetById[id]).filter((tweet): tweet is Tweet => Boolean(tweet)),
    [tweetById, tweetIds],
  );

  const patchTweet = useCallback((tweetId: number, patch: Partial<Tweet>) => {
    setTweetById((current) => {
      const existing = current[tweetId];
      if (!existing) {
        return current;
      }
      return { ...current, [tweetId]: { ...existing, ...patch } };
    });
  }, []);

  const removeTweet = useCallback((tweetId: number) => {
    setTweetIds((ids) => ids.filter((id) => id !== tweetId));
    setTweetById((current) => {
      const next = { ...current };
      delete next[tweetId];
      return next;
    });
  }, []);

  const removeByAuthor = useCallback((authorId: number) => {
    setTweetById((current) => {
      const removed = new Set(
        Object.values(current)
          .filter((tweet) => tweet.author.id === authorId)
          .map((tweet) => tweet.id),
      );
      setTweetIds((ids) => ids.filter((id) => !removed.has(id)));
      const next = { ...current };
      removed.forEach((id) => delete next[id]);
      return next;
    });
  }, []);

  const load = useCallback(
    async (cursor?: string | null, append = false) => {
      if (!tag) {
        return;
      }
      setLoading(true);
      setError("");
      try {
        const next = await getHashtagPosts(tag, cursor);
        setPage(next);
        setTweetById((current) => {
          const map = append ? { ...current } : {};
          for (const tweet of next.items) {
            map[tweet.id] = tweet;
          }
          return map;
        });
        setTweetIds((current) => {
          const ids = next.items.map((tweet) => tweet.id);
          if (!append) {
            return ids;
          }
          const existing = new Set(current);
          return [...current, ...ids.filter((id) => !existing.has(id))];
        });
        void recordPostViews(next.items.map((tweet) => tweet.id));
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [tag],
  );

  // Reset when navigating to a different tag, then load its first page.
  useEffect(() => {
    setTweetIds([]);
    setTweetById({});
    setPage(null);
    void load();
  }, [load]);

  return (
    <>
      <header className="feed-header">
        <div className="detail-toolbar">
          <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="hashtag-title">#{tag}</h1>
        </div>
      </header>

      {error ? <div className="status-panel error">{error}</div> : null}
      {!loading && tweets.length === 0 && !error ? (
        <div className="status-panel">No posts with this hashtag yet.</div>
      ) : null}
      <section className="tweet-list" aria-live="polite">
        {tweets.map((tweet) => (
          <TweetCard
            key={tweet.id}
            tweet={tweet}
            onOpen={() => navigate(`/tweet/${tweet.id}`)}
            onTweetPatch={patchTweet}
            currentUserId={currentUser.id}
            onDeleted={removeTweet}
            onAuthorMuted={removeByAuthor}
            onAuthorBlocked={removeByAuthor}
          />
        ))}
      </section>
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : null}
      {page?.next_cursor ? (
        <button
          className="load-more"
          onClick={() => void load(page.next_cursor, true)}
          disabled={loading}
        >
          Load more
        </button>
      ) : null}
    </>
  );
}

const NOTIFICATION_TEXT: Record<Notification["type"], string> = {
  like: "liked your tweet",
  retweet: "quoted your post",
  comment: "commented on your tweet",
  reply: "replied to your comment",
  follow: "followed you",
  mention: "mentioned you",
};

function NotificationIcon({ type }: { type: Notification["type"] }) {
  if (type === "follow") {
    return <UserPlus size={16} className="notif-icon follow" aria-hidden="true" />;
  }
  if (type === "retweet") {
    return <Repeat2 size={16} className="notif-icon retweet" aria-hidden="true" />;
  }
  if (type === "mention") {
    return <AtSign size={16} className="notif-icon mention" aria-hidden="true" />;
  }
  if (type === "comment" || type === "reply") {
    return <MessageCircle size={16} className="notif-icon comment" aria-hidden="true" />;
  }
  return <Heart size={16} className="notif-icon like" aria-hidden="true" />;
}

function NotificationsView() {
  const { refreshUnread } = useOutletContext<LayoutContext>();
  const [items, setItems] = useState<Notification[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    async (nextCursor?: string | null, append = false) => {
      setLoading(true);
      setError("");
      try {
        const page = await listNotifications(nextCursor);
        setItems((current) => (append ? [...current, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const hasUnread = items.some((notification) => !notification.is_read);

  async function markOneRead(notification: Notification) {
    if (notification.is_read) {
      return;
    }
    // Optimistic: reflect it immediately, and re-read the badge count.
    setItems((current) =>
      current.map((item) =>
        item.id === notification.id ? { ...item, is_read: true } : item,
      ),
    );
    try {
      await markNotificationRead(notification.id);
      refreshUnread();
    } catch {
      // Roll back on failure so the row doesn't lie about being read.
      setItems((current) =>
        current.map((item) =>
          item.id === notification.id ? { ...item, is_read: false } : item,
        ),
      );
    }
  }

  async function markAllRead() {
    setItems((current) => current.map((item) => ({ ...item, is_read: true })));
    try {
      await markNotificationsRead();
      refreshUnread();
    } catch (err) {
      setError(getErrorMessage(err));
      void load();
    }
  }

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Notifications</h1>
          {hasUnread ? (
            <button className="text-button" onClick={() => void markAllRead()}>
              Mark all read
            </button>
          ) : null}
        </div>
      </header>
      {error ? <div className="status-panel error">{error}</div> : null}
      {!loading && items.length === 0 && !error ? (
        <div className="status-panel">No notifications yet.</div>
      ) : null}
      <ul className="notif-list">
        {items.map((notification) => {
          const to =
            notification.type === "follow"
              ? `/${encodeURIComponent(notification.actor.username)}`
              : notification.tweet_id !== null
                ? `/tweet/${notification.tweet_id}`
                : "#";
          return (
            <li
              key={notification.id}
              className={notification.is_read ? "notif-item" : "notif-item unread"}
            >
              <Link
                to={to}
                className="notif-link"
                onClick={() => void markOneRead(notification)}
              >
                <NotificationIcon type={notification.type} />
                <Avatar user={notification.actor} size="small" />
                <div className="notif-body">
                  <p>
                    <strong>@{notification.actor.username}</strong>{" "}
                    {NOTIFICATION_TEXT[notification.type]}
                  </p>
                  {notification.preview ? (
                    <p className="notif-preview">{notification.preview}</p>
                  ) : null}
                  <span className="notif-time">
                    {formatCompactDate(notification.created_at)}
                  </span>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
      {cursor ? (
        <button
          className="load-more"
          onClick={() => void load(cursor, true)}
          disabled={loading}
        >
          Load more
        </button>
      ) : null}
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading notifications</span>
        </div>
      ) : null}
    </>
  );
}

function HomeView() {
  const { currentUser, refreshToken } = useOutletContext<LayoutContext>();
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

  const removeTweet = useCallback((tweetId: number) => {
    setTweetIds((ids) => ids.filter((id) => id !== tweetId));
    setTweetById((current) => {
      const next = { ...current };
      delete next[tweetId];
      return next;
    });
  }, []);

  // After muting/blocking an author, drop every post of theirs from the feed --
  // not just the card the action came from.
  const removeTweetsByAuthor = useCallback(
    (authorId: number) => {
      const removedIds = new Set(
        Object.values(tweetById)
          .filter((tweet) => tweet.author.id === authorId)
          .map((tweet) => tweet.id),
      );
      setTweetIds((ids) => ids.filter((id) => !removedIds.has(id)));
      setTweetById((current) => {
        const next = { ...current };
        removedIds.forEach((id) => delete next[id]);
        return next;
      });
    },
    [tweetById],
  );

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
        void recordPostViews(nextPage.items.map((tweet) => tweet.id));
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

      <Composer currentUser={currentUser} onPosted={insertPostedTweet} />

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
            currentUserId={currentUser.id}
            onDeleted={removeTweet}
            onAuthorMuted={removeTweetsByAuthor}
            onAuthorBlocked={removeTweetsByAuthor}
            onQuoted={insertPostedTweet}
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
  const location = useLocation();
  const { currentUser } = useOutletContext<LayoutContext>();
  const scrollToPostId = (location.state as { scrollToPostId?: number } | null)
    ?.scrollToPostId;
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
        if (!cancelled) {
          setTweet(loaded);
          void recordPostViews([numericTweetId]);
        }
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
  return (
    <TweetDetail
      tweet={tweet}
      onBack={() => navigate(-1)}
      onTweetPatch={patchTweet}
      scrollToPostId={scrollToPostId}
      currentUserId={currentUser.id}
      onDeleted={() => navigate("/")}
    />
  );
}

function ThemeToggle({
  theme,
  onToggleTheme,
  className = "",
  iconOnly = false,
}: {
  theme: Theme;
  onToggleTheme: () => void;
  className?: string;
  iconOnly?: boolean;
}) {
  const Icon = theme === "dark" ? Sun : Moon;
  const nextTheme = theme === "dark" ? "light" : "dark";
  const baseClass = iconOnly ? "icon-button" : "theme-toggle";

  return (
    <button
      className={`${baseClass} ${className}`.trim()}
      onClick={onToggleTheme}
      aria-label={`Use ${nextTheme} mode`}
      title={`Use ${nextTheme} mode`}
    >
      <Icon size={iconOnly ? 18 : 19} aria-hidden="true" />
      {iconOnly ? null : <span>{theme === "dark" ? "Light" : "Dark"}</span>}
    </button>
  );
}

function Composer({
  currentUser,
  onPosted,
}: {
  currentUser: UserSummary;
  onPosted: (tweet: Tweet) => void;
}) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [posting, setPosting] = useState(false);
  const [visibility, setVisibility] = useState<TweetVisibility>("public");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(content, setContent, 280);
  const media = useMediaAttachment();
  const remaining = 280 - content.length;
  const canPost = (content.trim().length > 0 || media.mediaUrls.length > 0) && remaining >= 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canPost || media.uploading) {
      return;
    }

    setPosting(true);
    setError("");
    try {
      const tweet = await createTweet(content.trim(), media.mediaUrls, undefined, visibility);
      setContent("");
      media.clear();
      setVisibility("public");
      onPosted(tweet);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setPosting(false);
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <Avatar user={currentUser} />
      <div className="composer-body">
        <textarea
          {...fieldProps}
          value={content}
          maxLength={280}
          placeholder="What is happening?"
          aria-label="Tweet content"
        />
        <MediaPreview attachment={media} />
        {error ? <p className="form-error">{error}</p> : null}
        <div className="composer-visibility">
          <VisibilityPicker value={visibility} onChange={setVisibility} disabled={posting} />
        </div>
        <div className="composer-actions">
          <div className="composer-tools">
            <EmojiPicker onSelect={insertEmoji} />
            <MediaButton attachment={media} />
          </div>
          <span className={remaining < 30 ? "counter warn" : "counter"}>{remaining}</span>
          <button className="primary-button compact" disabled={posting || media.uploading || !canPost}>
            {posting ? "Posting..." : "Post"}
          </button>
        </div>
      </div>
    </form>
  );
}

function TweetDetail({
  tweet,
  onBack,
  onTweetPatch,
  scrollToPostId,
  currentUserId,
  onDeleted,
}: {
  tweet: Tweet;
  onBack: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
  scrollToPostId?: number;
  currentUserId: number;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editVisibility, setEditVisibility] = useState<TweetVisibility>(tweet.visibility);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const isOwn = tweet.author.id === currentUserId;

  function startEditing() {
    setEditVisibility(tweet.visibility);
    setEditing(true);
  }

  async function saveEdit(content: string, mediaUrls: string[]) {
    setSavingEdit(true);
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
      setSavingEdit(false);
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    try {
      await deleteTweet(tweet.id);
      onDeleted();
    } catch (err) {
      setError(getErrorMessage(err));
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }
  const [comments, setComments] = useState<Comment[]>([]);
  const [replying, setReplying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<"like" | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [error, setError] = useState("");
  const commentIdsKey = useMemo(
    () => comments.map((item) => item.id).join(","),
    [comments],
  );

  // Nesting depth per comment. Comments arrive in thread (pre-order) order, so
  // a comment's parent always precedes it and its depth is already known.
  const depthByCommentId = useMemo(() => {
    const depths = new Map<number, number>();
    for (const item of comments) {
      const depth =
        item.parent_comment_id == null
          ? 0
          : (depths.get(item.parent_comment_id) ?? 0) + 1;
      depths.set(item.id, depth);
    }
    return depths;
  }, [comments]);

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
      const loaded = await listComments(tweet.id);
      setComments(loaded);
      void recordPostViews(loaded.map((c) => c.id));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [tweet.id]);

  useEffect(() => {
    void loadTweetComments();
  }, [loadTweetComments]);

  // When opened from the profile "Replies" tab, scroll to the specific post the
  // reply was made to (the root tweet or a comment in the thread) once loaded.
  useEffect(() => {
    if (scrollToPostId === undefined || loading) {
      return;
    }
    const target = document.getElementById(`post-${scrollToPostId}`);
    if (!target) {
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.classList.add("post-scroll-highlight");
    const timer = window.setTimeout(
      () => target.classList.remove("post-scroll-highlight"),
      2200,
    );
    return () => window.clearTimeout(timer);
  }, [scrollToPostId, loading]);

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

  function handleDetailQuoted() {
    onTweetPatch(tweet.id, { retweet_count: tweet.retweet_count + 1 });
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

  // Errors propagate to ReplyComposer, which shows them inside the modal.
  async function submitDetailComment(content: string, mediaUrls: string[]) {
    await createComment(tweet.id, content, mediaUrls);
    onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
    await loadTweetComments();
  }

  return (
    <section className="tweet-detail" aria-labelledby="tweet-detail-title">
      <div className="detail-toolbar">
        <button className="icon-button" onClick={onBack} aria-label="Back to timeline">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <h2 id="tweet-detail-title">Tweet</h2>
      </div>

      <article id={`post-${tweet.id}`} className="detail-tweet">
        {isOwn ? (
          <PostMenu
            onEdit={startEditing}
            onDelete={() => setConfirmingDelete(true)}
          />
        ) : null}
        <div className="detail-author">
          <Link
            to={`/${encodeURIComponent(tweet.author.username)}`}
            className="author-link"
            aria-label={`View profile of ${tweet.author.username}`}
          >
            <Avatar user={tweet.author} />
          </Link>
          <div>
            <Link
              to={`/${encodeURIComponent(tweet.author.username)}`}
              className="author-link"
            >
              <strong>@{tweet.author.username}</strong>
            </Link>
            <div className="detail-meta">
              <span>{displayDate}</span>
              {tweet.edited_at ? <span className="edited-tag">· edited</span> : null}
              <VisibilityBadge visibility={tweet.visibility} />
            </div>
          </div>
        </div>
        {editing ? (
          <PostEditor
            initialContent={tweet.content}
            initialMedia={tweet.media_urls}
            maxLength={280}
            saving={savingEdit}
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
        <div className="tweet-actions detail-actions">
          <button
            className="tweet-action comment"
            onClick={() => setReplying(true)}
            aria-label="Reply"
          >
            <MessageCircle size={18} aria-hidden="true" />
            <span>{tweet.comment_count}</span>
          </button>
          <button
            className="tweet-action retweet"
            onClick={() => setQuoting(true)}
            aria-label="Quote"
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
          <span className="tweet-action views" aria-label="Views">
            <BarChart2 size={18} aria-hidden="true" />
            <span>{tweet.view_count}</span>
          </span>
        </div>
      </article>

      {replying ? (
        <ReplyComposer
          target={tweet}
          onClose={() => setReplying(false)}
          onSubmit={submitDetailComment}
        />
      ) : null}

      <section className="comment-list" aria-label="Comments">
        {loading ? (
          <div className="loading-row">
            <Loader2 className="spin" size={18} aria-hidden="true" />
            <span>Loading comments</span>
          </div>
        ) : null}
        {comments.map((item) => (
          <CommentCard
            key={item.id}
            comment={item}
            currentUserId={currentUserId}
            depth={depthByCommentId.get(item.id) ?? 0}
            onChanged={() => {
              void loadTweetComments();
            }}
            onReplyCreated={() => {
              onTweetPatch(tweet.id, { comment_count: tweet.comment_count + 1 });
            }}
          />
        ))}
      </section>
      {confirmingDelete ? (
        <ConfirmDialog
          title="Delete Tweet?"
          message="This can't be undone and it will be removed from your profile, the timeline, and any threads it started."
          confirmLabel="Delete"
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}
      {quoting ? (
        <QuoteComposer
          quoted={tweet}
          onClose={() => setQuoting(false)}
          onQuoted={handleDetailQuoted}
        />
      ) : null}
    </section>
  );
}

function SidebarSearchBar() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setShowHistory(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function doSearch(term: string) {
    if (!term.trim()) return;
    addSearchHistory(term.trim());
    setHistoryKey((k) => k + 1);
    setQuery("");
    setShowHistory(false);
    navigate(`/search?q=${encodeURIComponent(term.trim())}`);
  }

  return (
    <div className="sidebar-search" ref={containerRef}>
      <label className="search-box">
        <Search size={18} aria-hidden="true" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => setShowHistory(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              doSearch(query);
            }
          }}
          placeholder="Search"
          aria-label="Search"
        />
      </label>
      {showHistory && !query.trim() ? (
        <SearchHistoryPanel
          key={historyKey}
          onSelect={(term) => {
            doSearch(term);
          }}
          onChanged={() => setHistoryKey((k) => k + 1)}
        />
      ) : null}
    </div>
  );
}

function TrendingPanel() {
  const [trends, setTrends] = useState<TrendingHashtag[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getTrending()
      .then((items) => {
        if (!cancelled) setTrends(items);
      })
      .catch(() => {
        // Trending is a nicety; on failure the panel just stays empty.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Nothing to show yet (fresh install, or the fetch failed): hide the panel
  // rather than render an empty heading.
  if (!loading && trends.length === 0) {
    return null;
  }

  return (
    <section className="discovery-panel trending-panel" aria-labelledby="trending-title">
      <h2 id="trending-title">Trending</h2>
      {loading ? (
        <div className="loading-row small">
          <Loader2 className="spin" size={16} aria-hidden="true" />
        </div>
      ) : (
        <ul className="trend-list">
          {trends.map((trend) => (
            <li key={trend.tag}>
              <Link className="trend-row" to={`/hashtag/${encodeURIComponent(trend.tag)}`}>
                <span className="trend-tag">#{trend.tag}</span>
                <span className="trend-count">
                  {trend.post_count} {trend.post_count === 1 ? "post" : "posts"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function UserDiscoveryPanel({
  onChanged,
  hideHeading = false,
  hideSearch = false,
  initialQuery = "",
}: {
  onChanged: () => void;
  hideHeading?: boolean;
  hideSearch?: boolean;
  initialQuery?: string;
}) {
  const [query, setQuery] = useState(initialQuery);

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);
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
    <section
      className="discovery-panel"
      aria-labelledby={hideHeading ? undefined : "discover-title"}
      aria-label={hideHeading ? "People" : undefined}
    >
      {hideHeading ? null : <h2 id="discover-title">People</h2>}
      {hideSearch ? null : (
        <label className="search-box">
          <Search size={18} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search users"
            aria-label="Search users"
          />
        </label>
      )}
      {error ? <p className="form-error">{error}</p> : null}
      <div className="user-list">
        {users.map((user) => (
          <div className="user-row" key={user.id}>
            <Link
              to={`/${encodeURIComponent(user.username)}`}
              className="author-link user-row-link"
              aria-label={`View profile of ${user.username}`}
            >
              <Avatar user={user} size="small" />
              <div className="user-copy">
                <strong>{displayName(user)}</strong>
                <span>@{user.username}</span>
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
