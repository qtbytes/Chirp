import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell,
  Feather,
  Home,
  Loader2,
  LogOut,
  Moon,
  Search,
  Sun,
  UserPlus,
  Users,
} from "lucide-react";
import {
  ApiError,
  createTweet,
  followUser,
  getCurrentUser,
  getTimeline,
  listUsers,
  login,
  logout,
  register,
  unfollowUser,
} from "./api";
import type { TimelineKind, TimelinePage, Tweet, UserDiscovery, UserSummary } from "./types";

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
              onClick={() => setActiveTab("for-you")}
              role="tab"
              aria-selected={activeTab === "for-you"}
            >
              For you
            </button>
            <button
              className={activeTab === "following" ? "tab active" : "tab"}
              onClick={() => setActiveTab("following")}
              role="tab"
              aria-selected={activeTab === "following"}
            >
              Following
            </button>
          </div>
        </header>

        <Composer onPosted={() => setRefreshToken((value) => value + 1)} />

        {feedError ? <div className="status-panel error">{feedError}</div> : null}
        {!loadingFeed && tweets.length === 0 && !feedError ? (
          <div className="status-panel">No tweets yet.</div>
        ) : null}
        <section className="tweet-list" aria-live="polite">
          {tweets.map((tweet) => (
            <TweetCard key={tweet.id} tweet={tweet} />
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

function TweetCard({ tweet }: { tweet: Tweet }) {
  const displayDate = useMemo(() => {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(tweet.created_at));
  }, [tweet.created_at]);

  return (
    <article className="tweet-card">
      <div className="avatar" aria-hidden="true">
        {tweet.author.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="tweet-body">
        <header>
          <strong>@{tweet.author.username}</strong>
          <span>{displayDate}</span>
        </header>
        <p>{tweet.content}</p>
        <footer>
          <span>{tweet.like_count} likes</span>
          <span>{tweet.comment_count} comments</span>
        </footer>
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

export default App;
