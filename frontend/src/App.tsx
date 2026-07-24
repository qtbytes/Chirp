import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  AtSign,
  BarChart2,
  Bell,
  Eye,
  EyeOff,
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
  Shield,
  Sun,
  UserPlus,
  X,
} from "lucide-react";
import {
  ApiError,
  createComment,
  createTweet,
  deleteTweet,
  displayName,
  editTweet,
  blockUser,
  followUser,
  muteUser,
  getComment,
  getCommentStats,
  getCurrentUser,
  getTimeline,
  getTweet,
  getTweetStats,
  getUserProfile,
  recordPostViews,
  getDmUnreadCount,
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
import type { HashtagSort } from "./api";
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
  UserProfile,
  UserSummary,
} from "./types";
import {
  Avatar,
  CommentCard,
  ConfirmDialog,
  CurrentUserProvider,
  MediaButton,
  MediaGallery,
  BASE_POST_LENGTH,
  ComposerHighlight,
  ComposerLimitNotice,
  VERIFIED_EMAIL_POST_LENGTH,
  counterClass,
  useComposerTypeahead,
  usePostLength,
  MediaPreview,
  PostEditor,
  PostMenu,
  PostBody,
  QuoteComposer,
  ReportModal,
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
import ModerationView from "./ModerationView";
import { ProfileView } from "./ProfileView";
import { FollowListView } from "./FollowListView";
import { ChatView, MessagesView } from "./MessagesView";
import { AccountView } from "./AccountView";
import { ResetPasswordView, VerifyEmailView } from "./AuthTokenViews";
import { ForgotPasswordModal } from "./ForgotPasswordModal";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import { useFeedMemory } from "./useFeedMemory";
import { useMediaAttachment } from "./useMediaAttachment";
import { InfiniteScroll } from "./InfiniteScroll";

type AuthMode = "login" | "register";
type Theme = "light" | "dark";
type LayoutContext = {
  currentUser: UserSummary;
  refreshToken: number;
  onDiscoveryChanged: () => void;
  refreshUnread: () => void;
  /** Detail views publish the post's participants here; the sidebar swaps its
      generic People list for a Twitter-style "Relevant people" panel. */
  setRelevantPeople: (users: UserSummary[]) => void;
};

const THEME_STORAGE_KEY = "twitter-system-theme";

function getSystemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Where a history entry's viewport was when it was left. The raw offset alone
 * is not enough to return exactly: lazily loaded images reflow the list while
 * it re-renders, shifting every absolute offset. So the topmost visible post
 * card (`#post-…`) is recorded too, with its exact viewport delta — restoring
 * pins that element back to the same place regardless of how the content
 * above it resized.
 */
type ScrollRecord = {
  y: number;
  anchorId: string | null;
  /** The anchor's getBoundingClientRect().top at capture time. */
  anchorTop: number;
};

const scrollPositions = new Map<string, ScrollRecord>();

function captureScrollRecord(): ScrollRecord {
  const record: ScrollRecord = { y: window.scrollY, anchorId: null, anchorTop: 0 };
  for (const el of document.querySelectorAll<HTMLElement>("article[id^='post-']")) {
    const rect = el.getBoundingClientRect();
    if (rect.bottom > 0 && rect.top < window.innerHeight) {
      record.anchorId = el.id;
      record.anchorTop = rect.top;
      break;
    }
  }
  return record;
}

/**
 * Twitter-style scroll restoration for the plain (non-data) router: going
 * back/forward returns to the exact spot the page was left at — back from a
 * comment's detail lands on that comment, not the top of the thread — while
 * ordinary navigation still starts new pages at the top.
 */
function ScrollMemory() {
  const location = useLocation();
  const navigationType = useNavigationType();
  // The record as of the last scroll event. Captured continuously instead of
  // at save time because by then the next page's (shorter) DOM is already in
  // and both the offset and the anchor are gone.
  const lastRecord = useRef<ScrollRecord>({ y: 0, anchorId: null, anchorTop: 0 });

  useEffect(() => {
    window.history.scrollRestoration = "manual";
    let frame = 0;
    const capture = () => {
      frame = 0;
      lastRecord.current = captureScrollRecord();
    };
    const onScroll = () => {
      if (!frame) {
        frame = requestAnimationFrame(capture);
      }
    };
    capture();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) {
        cancelAnimationFrame(frame);
      }
    };
  }, []);

  // Remember where this history entry was when it is left…
  useLayoutEffect(() => {
    const key = location.key;
    return () => {
      scrollPositions.set(key, lastRecord.current);
    };
  }, [location.key]);

  // …and put the viewport back there when it is returned to.
  useLayoutEffect(() => {
    const saved = navigationType === "POP" ? scrollPositions.get(location.key) : undefined;
    if (!saved || (saved.y === 0 && !saved.anchorId)) {
      window.scrollTo(0, 0);
      lastRecord.current = { y: 0, anchorId: null, anchorTop: 0 };
      return;
    }

    // Keep the saved anchor pinned at its saved viewport position while the
    // returning page re-renders and its images stream in (each load reflows
    // the list). The pinning stops at the deadline — or immediately once the
    // user scrolls/keys/touches, so it never fights real input.
    let done = false;
    const deadline = performance.now() + 3000;
    const cancelEvents = ["wheel", "touchstart", "keydown", "mousedown"] as const;
    const stop = () => {
      if (done) {
        return;
      }
      done = true;
      for (const name of cancelEvents) {
        window.removeEventListener(name, stop);
      }
    };
    for (const name of cancelEvents) {
      window.addEventListener(name, stop, { passive: true });
    }

    const pin = () => {
      if (done) {
        return;
      }
      if (saved.anchorId) {
        // Ids can rarely repeat (a parent tweet shown under two replies);
        // pin whichever instance implies a position closest to the saved one.
        const candidates = document.querySelectorAll<HTMLElement>(
          `[id='${CSS.escape(saved.anchorId)}']`,
        );
        let best: { drift: number; dist: number } | null = null;
        for (const el of candidates) {
          const drift = el.getBoundingClientRect().top - saved.anchorTop;
          const dist = Math.abs(window.scrollY + drift - saved.y);
          if (!best || dist < best.dist) {
            best = { drift, dist };
          }
        }
        if (best && Math.abs(best.drift) > 1) {
          window.scrollBy(0, best.drift);
        }
        if (!best) {
          // Anchor not rendered (yet, or content changed): approximate by
          // offset while waiting for it.
          const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
          window.scrollTo(0, Math.min(saved.y, Math.max(maxScroll, 0)));
        }
      } else {
        const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
        window.scrollTo(0, Math.min(saved.y, Math.max(maxScroll, 0)));
      }
      if (performance.now() < deadline) {
        requestAnimationFrame(pin);
      } else {
        stop();
      }
    };
    pin();
    return stop;
  }, [location.key, navigationType]);

  return null;
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
    <ScrollMemory />
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
        {/* Only mounted for moderators; anyone else typing the URL falls into
            the catch-all redirect. The API 404s them regardless. */}
        {currentUser.is_moderator ? (
          <>
            <Route path="/moderation" element={<ModerationView />} />
            <Route path="/moderation/resolved" element={<ModerationView />} />
          </>
        ) : null}
        <Route path="/messages" element={<MessagesView currentUser={currentUser} />} />
        <Route
          path="/messages/:username"
          element={<ChatView currentUser={currentUser} />}
        />
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
        <Route path="/comment/:commentId" element={<CommentDetailRoute />} />
        <Route
          path="/:username"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route
          path="/:username/replies"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route
          path="/:username/media"
          element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
        />
        <Route
          path="/:username/likes"
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
  const [confirmPassword, setConfirmPassword] = useState("");
  // Only after the field has been left alone: complaining "doesn't match" at the
  // first keystroke of a password still being typed is noise, not help.
  const [confirmBlurred, setConfirmBlurred] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);

  const confirming = mode === "register";
  const passwordsMatch = password === confirmPassword;
  // Shown under the field once it has been blurred, and withdrawn the moment
  // the two agree -- so a correction is confirmed as it is typed.
  const showMismatch = confirming && confirmBlurred && !passwordsMatch;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // A typo here is not recoverable for an account with no email address:
    // change-password and change-email both require the current password, and
    // forgot-password only mails a confirmed one. Caught before the request,
    // so the account is never created with a password the user cannot repeat.
    if (confirming && !passwordsMatch) {
      setConfirmBlurred(true);
      setError("");
      return;
    }

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
            {/* One field, two jobs: registering names the account, logging in
                identifies it -- and either the name or a confirmed address
                will do. Hence the wider cap when logging in: an address can
                run to 254 characters where a username stops at 50. */}
            <span>{mode === "login" ? "Username or email" : "Username"}</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              minLength={3}
              maxLength={mode === "login" ? 254 : 50}
              autoComplete="username"
              required
            />
          </label>
          {mode === "register" ? (
            <div className="auth-field">
              <label>
                <span>
                  Email <span className="label-optional">(optional)</span>
                </span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  maxLength={254}
                  autoComplete="email"
                  aria-describedby="email-hint"
                />
              </label>
              {/* Sits with the field it is about, and names its own subject: as
                  a note at the foot of the form it read as if "it" were the
                  password directly above it. */}
              <p className="form-hint" id="email-hint">
                Confirming an address raises your posts from {BASE_POST_LENGTH} to{" "}
                {VERIFIED_EMAIL_POST_LENGTH} characters, and it is the only way to
                reset a forgotten password. We'll send a link.
              </p>
            </div>
          ) : null}
          {/* The reveal button is a sibling of the label, not a child of it:
              inside, its own name joins the label's, and the input announces
              itself as "Password Show password". It is positioned from the
              bottom so it centres on the input whatever the label above does. */}
          <div className="password-field">
            <label>
              <span>Password</span>
              <input
                type={revealed ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={8}
                maxLength={128}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
              />
            </label>
            {/* One control for both password fields: revealing to check a typo
                is pointless if the copy you are checking against stays hidden.
                It catches the mistakes a confirm field cannot -- the same typo
                made twice, or the same clipboard pasted into both. */}
            <button
              type="button"
              className="password-reveal"
              onClick={() => setRevealed((value) => !value)}
              aria-label={revealed ? "Hide password" : "Show password"}
              aria-pressed={revealed}
            >
              {revealed ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {confirming ? (
            <div className="auth-field">
              <label>
                <span>Confirm password</span>
                <input
                  type={revealed ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  onBlur={() => setConfirmBlurred(true)}
                  maxLength={128}
                  autoComplete="new-password"
                  aria-invalid={showMismatch}
                  aria-describedby={showMismatch ? "confirm-error" : undefined}
                  required
                />
              </label>
              {showMismatch ? (
                <p className="form-error" id="confirm-error">
                  Those passwords don't match.
                </p>
              ) : null}
            </div>
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
            // The confirm field is unmounting; leaving its state behind would
            // arm a stale mismatch against the next draft that mounts it.
            setConfirmPassword("");
            setConfirmBlurred(false);
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
  const [dmUnread, setDmUnread] = useState(0);
  const [composing, setComposing] = useState(false);
  const [relevantPeople, setRelevantPeople] = useState<UserSummary[]>([]);
  const location = useLocation();
  const isSearchRoute = location.pathname === "/search";
  const isNotificationsRoute = location.pathname === "/notifications";
  const isMessagesRoute = location.pathname.startsWith("/messages");
  const isSettingsRoute = location.pathname === "/settings";
  const isModerationRoute = location.pathname.startsWith("/moderation");
  const isHomeRoute =
    location.pathname === "/" || location.pathname === "/following";
  const hideDiscovery =
    isSearchRoute ||
    isNotificationsRoute ||
    isMessagesRoute ||
    isSettingsRoute ||
    isModerationRoute;
  const onDiscoveryChanged = () => setRefreshToken((value) => value + 1);

  const refreshUnread = useCallback(async () => {
    try {
      const { count } = await getUnreadNotificationCount();
      setUnread(count);
    } catch {
      // Ignore polling failures; the badge just keeps its last value.
    }
    // A DM send publishes the same SSE nudge as a notification, so both
    // badges re-read on the same triggers.
    try {
      const { count } = await getDmUnreadCount();
      setDmUnread(count);
    } catch {
      // Same: keep the last value.
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
            className={isMessagesRoute ? "rail-link active" : "rail-link"}
            to="/messages"
            aria-label={dmUnread > 0 ? `Messages, ${dmUnread} unread` : "Messages"}
          >
            <span className="rail-icon">
              <MessageCircle size={22} aria-hidden="true" />
              {dmUnread > 0 ? (
                <span className="rail-badge" aria-hidden="true">
                  {dmUnread > 99 ? "99+" : dmUnread}
                </span>
              ) : null}
            </span>
            <span>Messages</span>
          </Link>
          {currentUser.is_moderator ? (
            <Link
              className={isModerationRoute ? "rail-link active" : "rail-link"}
              to="/moderation"
            >
              <Shield size={22} aria-hidden="true" />
              <span>Moderation</span>
            </Link>
          ) : null}
          <Link
            className={isSettingsRoute ? "rail-link active" : "rail-link"}
            to="/settings"
          >
            <Settings size={22} aria-hidden="true" />
            <span>Settings</span>
          </Link>
        </nav>
        <button className="rail-post-button" onClick={() => setComposing(true)}>
          <Feather className="rail-post-icon" size={20} aria-hidden="true" />
          <span className="rail-post-label">Post</span>
        </button>
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
          context={{ currentUser, refreshToken, onDiscoveryChanged, refreshUnread, setRelevantPeople } satisfies LayoutContext}
        />
      </main>

      {hideDiscovery ? null : (
        <aside className="discovery-column">
          <SidebarSearchBar />
          {relevantPeople.length > 0 ? (
            <RelevantPeoplePanel
              users={relevantPeople}
              onChanged={onDiscoveryChanged}
              refreshToken={refreshToken}
            />
          ) : null}
          <TrendingPanel />
          {relevantPeople.length > 0 ? null : (
            <UserDiscoveryPanel onChanged={onDiscoveryChanged} hideSearch />
          )}
        </aside>
      )}

      {composing ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setComposing(false)}
        >
          <div
            className="compose-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Compose post"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="compose-modal-head">
              <button
                type="button"
                className="icon-button"
                onClick={() => setComposing(false)}
                aria-label="Close"
              >
                <X size={20} aria-hidden="true" />
              </button>
            </div>
            <Composer
              currentUser={currentUser}
              autoFocus
              onPosted={() => {
                setComposing(false);
                // Bump the shared refresh token so an open timeline picks the
                // new post up (the modal has no feed of its own to insert into).
                onDiscoveryChanged();
              }}
            />
          </div>
        </div>
      ) : null}
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
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") ?? "";
  const urlTab = searchParams.get("f");
  const [query, setQuery] = useState(initialQuery);
  const [tab, setTab] = useState<SearchTab>(
    urlTab === "latest" || urlTab === "people" ? urlTab : "top",
  );
  const [historyKey, setHistoryKey] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeQuery = query.trim();

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    setTab(urlTab === "latest" || urlTab === "people" ? urlTab : "top");
  }, [urlTab]);

  // Mirror the executed search into the URL (replace, not push) so coming
  // *back* to /search restores the same query and tab — without it the view
  // remounts empty and there is nothing for the feed memory to restore.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams();
      if (activeQuery) {
        next.set("q", activeQuery);
        if (tab !== "top") {
          next.set("f", tab);
        }
      }
      if (next.toString() !== searchParams.toString()) {
        setSearchParams(next, { replace: true });
      }
    }, 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeQuery, tab]);

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
    <article id={`post-${post.id}`} className="search-reply" onClick={onOpen}>
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
      <PostBody text={post.content} enablePreview={false} clamp />
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

  const takeFeedMemory = useFeedMemory(
    `search:${sort}:${query}`,
    { postById, ids, cursor },
    ids.length === 0,
  );

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
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [sort],
  );

  useEffect(() => {
    // Returning via back/forward restores the results the user left.
    const cached = takeFeedMemory();
    if (cached) {
      setPostById(cached.postById);
      setIds(cached.ids);
      setCursor(cached.cursor);
      setSearched(true);
      return;
    }
    setIds([]);
    setPostById({});
    setCursor(null);
    setSearched(false);
    const timer = window.setTimeout(() => {
      setSearched(true);
      void runSearch(query);
    }, 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
              onOpen={() => navigate(`/comment/${post.id}`)}
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
      <InfiniteScroll
        hasMore={!!cursor}
        loading={loading}
        onLoadMore={() => void runSearch(query, cursor, true)}
      />
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
  const [sort, setSort] = useState<HashtagSort>("top");

  const takeFeedMemory = useFeedMemory(
    `hashtag:${tag}:${sort}`,
    { page, tweetById, tweetIds },
    tweetIds.length === 0,
  );

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
        const next = await getHashtagPosts(tag, cursor, sort);
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
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [tag, sort],
  );

  // Reset when navigating to a different tag (or switching the sort), then
  // load the first page — unless we're returning via back/forward, which
  // restores the list as it was.
  useEffect(() => {
    const cached = takeFeedMemory();
    if (cached) {
      setPage(cached.page);
      setTweetById(cached.tweetById);
      setTweetIds(cached.tweetIds);
      return;
    }
    setTweetIds([]);
    setTweetById({});
    setPage(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        <div className="tab-list" role="tablist" aria-label="Hashtag feed sort">
          <button
            className={sort === "top" ? "tab active" : "tab"}
            onClick={() => setSort("top")}
            role="tab"
            aria-selected={sort === "top"}
          >
            Top
          </button>
          <button
            className={sort === "recent" ? "tab active" : "tab"}
            onClick={() => setSort("recent")}
            role="tab"
            aria-selected={sort === "recent"}
          >
            Latest
          </button>
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
      <InfiniteScroll
        hasMore={!!page?.next_cursor}
        loading={loading}
        onLoadMore={() => void load(page?.next_cursor, true)}
      />
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
  // Moderation notices are complete sentences: they render without the
  // "@actor" prefix, since the actor is the recipient, not the moderator.
  report_actioned: "A post you reported was removed for violating the rules.",
  post_removed: "Your post was removed for violating the rules.",
};

/** Notices that speak for the platform, not for another user. */
function isModerationNotice(type: Notification["type"]): boolean {
  return type === "report_actioned" || type === "post_removed";
}

function NotificationIcon({ type }: { type: Notification["type"] }) {
  if (isModerationNotice(type)) {
    return <Shield size={16} className="notif-icon moderation" aria-hidden="true" />;
  }
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

  const takeFeedMemory = useFeedMemory(
    "notifications",
    { items, cursor },
    items.length === 0,
  );

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
    const cached = takeFeedMemory();
    if (cached) {
      setItems(cached.items);
      setCursor(cached.cursor);
      setLoading(false);
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        <div className="empty-state">
          <Bell
            size={72}
            strokeWidth={1.4}
            className="empty-state-icon empty-state-icon--muted"
            aria-hidden="true"
          />
          <p>No notifications yet!</p>
        </div>
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
                className={
                  isModerationNotice(notification.type)
                    ? "notif-link notif-link--no-avatar"
                    : "notif-link"
                }
                onClick={() => void markOneRead(notification)}
              >
                <NotificationIcon type={notification.type} />
                {isModerationNotice(notification.type) ? null : (
                  <Avatar user={notification.actor} size="small" />
                )}
                <div className="notif-body">
                  <p>
                    {isModerationNotice(notification.type) ? null : (
                      <>
                        <strong>@{notification.actor.username}</strong>{" "}
                      </>
                    )}
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
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading notifications</span>
        </div>
      ) : null}
      <InfiniteScroll
        hasMore={!!cursor}
        loading={loading}
        onLoadMore={() => void load(cursor, true)}
      />
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

  const takeFeedMemory = useFeedMemory(
    `home:${activeTab}`,
    { page, tweetById, tweetIds },
    tweetIds.length === 0,
  );

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
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [activeTab],
  );

  useEffect(() => {
    // Arriving via back/forward restores the feed the user left, so the
    // scroll position (which may sit pages deep) still exists to return to.
    // Any other arrival — nav clicks, tab switches — fetches fresh.
    const cached = takeFeedMemory();
    if (cached) {
      setPage(cached.page);
      setTweetById(cached.tweetById);
      setTweetIds(cached.tweetIds);
      return;
    }
    void loadFeed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      {/* No title row here: the tabs say where you are, and on Home every
          vertical pixel above the composer is feed real estate. The h1 stays
          for screen readers and the document outline. */}
      <header className="feed-header">
        <h1 className="visually-hidden">Home</h1>
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
      <InfiniteScroll
        hasMore={!!page?.next_cursor}
        loading={loadingFeed}
        onLoadMore={() => void loadFeed(page?.next_cursor, true)}
      />
    </>
  );
}

/** Twitter-style dead-end for content that doesn't exist (deleted or bad id). */
function NotFoundPanel() {
  return (
    <div className="not-found-panel">
      <p>Hmm...this page doesn&rsquo;t exist. Try searching for something else.</p>
      <Link className="primary-button" to="/search">
        Search
      </Link>
    </div>
  );
}

function TweetDetailRoute() {
  const { tweetId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { currentUser, setRelevantPeople, refreshToken, onDiscoveryChanged } =
    useOutletContext<LayoutContext>();
  const scrollToPostId = (location.state as { scrollToPostId?: number } | null)
    ?.scrollToPostId;
  const numericTweetId = Number(tweetId);
  const [tweet, setTweet] = useState<Tweet | null>(null);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  // React 18+ mounts effects twice in StrictMode; without this guard a single
  // detail open would record two views in dev.
  const viewRecordedFor = useRef<number | null>(null);

  useEffect(() => {
    if (!Number.isInteger(numericTweetId) || numericTweetId <= 0) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    setNotFound(false);
    const alreadyRecorded = viewRecordedFor.current === numericTweetId;
    viewRecordedFor.current = numericTweetId;
    // Record the detail expand first so the count we fetch includes it.
    (alreadyRecorded ? Promise.resolve() : recordPostViews([numericTweetId]))
      .then(() => getTweet(numericTweetId))
      .then((loaded) => {
        if (!cancelled) {
          setTweet(loaded);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(getErrorMessage(err));
        }
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

  // Sidebar "Relevant people": the tweet's author while this detail is open.
  const author = tweet?.author;
  useEffect(() => {
    if (author && !author.is_deleted) {
      setRelevantPeople([author]);
    }
    return () => setRelevantPeople([]);
  }, [author?.id, setRelevantPeople]);

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
  if (notFound || (!error && !tweet)) {
    return <NotFoundPanel />;
  }
  if (error || !tweet) {
    return <div className="status-panel error">{error}</div>;
  }
  return (
    <TweetDetail
      tweet={tweet}
      onBack={() => navigate(-1)}
      onTweetPatch={patchTweet}
      scrollToPostId={scrollToPostId}
      currentUserId={currentUser.id}
      onDeleted={() => navigate("/")}
      refreshToken={refreshToken}
      onDiscoveryChanged={onDiscoveryChanged}
    />
  );
}

function CommentDetailRoute() {
  const { commentId } = useParams();
  const navigate = useNavigate();
  const { currentUser, setRelevantPeople } = useOutletContext<LayoutContext>();
  const numericCommentId = Number(commentId);
  const [comment, setComment] = useState<Comment | null>(null);
  const [rootAuthor, setRootAuthor] = useState<UserSummary | null>(null);
  const [thread, setThread] = useState<Comment[]>([]);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingReplies, setLoadingReplies] = useState(true);

  // React 18+ mounts effects twice in StrictMode; without this guard a single
  // detail open would record two views in dev.
  const viewRecordedFor = useRef<number | null>(null);

  // The replies API is thread-scoped, so load the focal comment's whole
  // thread and pick out its subtree below.
  const loadThread = useCallback(async (tweetId: number) => {
    setLoadingReplies(true);
    try {
      setThread(await listComments(tweetId));
    } catch {
      // Replies are secondary; the focal comment is already on screen.
    } finally {
      setLoadingReplies(false);
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const loaded = await getComment(numericCommentId);
      setComment(loaded);
      await loadThread(loaded.tweet_id);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, [numericCommentId, loadThread]);

  useEffect(() => {
    if (!Number.isInteger(numericCommentId) || numericCommentId <= 0) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    setNotFound(false);
    const alreadyRecorded = viewRecordedFor.current === numericCommentId;
    viewRecordedFor.current = numericCommentId;
    // Record the detail expand first so the count we fetch includes it.
    (alreadyRecorded ? Promise.resolve() : recordPostViews([numericCommentId]))
      .then(() => getComment(numericCommentId))
      .then((loaded) => {
        if (cancelled) return;
        setComment(loaded);
        void loadThread(loaded.tweet_id);
        // The root post's author feeds the sidebar's "Relevant people" panel;
        // the thread itself doesn't need the root tweet.
        getTweet(loaded.tweet_id)
          .then((rootTweet) => {
            if (!cancelled) setRootAuthor(rootTweet.author);
          })
          .catch(() => {
            // Sidebar nicety only; the comment view works without it.
          });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(getErrorMessage(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [numericCommentId, loadThread]);

  // Sidebar "Relevant people": the comment's author first, then the root
  // post's author, deduplicated when someone replies to their own thread.
  const commentAuthor = comment?.author;
  useEffect(() => {
    const people = [commentAuthor, rootAuthor].filter(
      (user, index, list): user is UserSummary =>
        user != null &&
        !user.is_deleted &&
        list.findIndex((other) => other?.id === user.id) === index,
    );
    if (people.length > 0) {
      setRelevantPeople(people);
    }
    return () => setRelevantPeople([]);
  }, [commentAuthor?.id, rootAuthor?.id, setRelevantPeople]);

  // The focal comment's subtree, with depth relative to it. The thread arrives
  // in pre-order, so a reply's parent is always seen before the reply.
  const replies = useMemo(() => {
    const depths = new Map<number, number>();
    const subtree: { item: Comment; depth: number }[] = [];
    for (const item of thread) {
      if (item.parent_comment_id === numericCommentId) {
        depths.set(item.id, 0);
        subtree.push({ item, depth: 0 });
      } else if (
        item.parent_comment_id != null &&
        depths.has(item.parent_comment_id)
      ) {
        const depth = (depths.get(item.parent_comment_id) ?? 0) + 1;
        depths.set(item.id, depth);
        subtree.push({ item, depth });
      }
    }
    return subtree;
  }, [thread, numericCommentId]);

  const statsIdsKey = useMemo(() => {
    if (!comment) return "";
    return [comment.id, ...replies.map(({ item }) => item.id)].join(",");
  }, [comment, replies]);

  useEffect(() => {
    if (!statsIdsKey) {
      return;
    }
    const idsToSync = statsIdsKey.split(",").map(Number);
    let cancelled = false;

    async function syncStats() {
      try {
        const stats = await getCommentStats(idsToSync);
        if (cancelled) {
          return;
        }
        setComment((current) =>
          current ? mergeCommentStats([current], stats)[0] : current,
        );
        setThread((current) => mergeCommentStats(current, stats));
      } catch {
        // background sync; ignore failures
      }
    }

    const timer = window.setInterval(() => void syncStats(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [statsIdsKey]);

  if (loading) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading</span>
      </div>
    );
  }
  if (notFound || (!error && !comment)) {
    return <NotFoundPanel />;
  }
  if (error || !comment) {
    return <div className="status-panel error">{error}</div>;
  }
  return (
    <section className="tweet-detail" aria-labelledby="comment-detail-title">
      <div className="detail-toolbar">
        <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <h2 id="comment-detail-title">Comment</h2>
      </div>
      <Link
        className="thread-context-link"
        to={`/tweet/${comment.tweet_id}`}
        state={{ scrollToPostId: comment.id }}
      >
        View in full thread
      </Link>
      <CommentCard
        comment={comment}
        currentUserId={currentUser.id}
        onChanged={() => void reload()}
        onReplyCreated={() => void reload()}
        focused
      />
      <section className="comment-list" aria-label="Replies">
        {loadingReplies ? (
          <div className="loading-row">
            <Loader2 className="spin" size={18} aria-hidden="true" />
            <span>Loading replies</span>
          </div>
        ) : null}
        {replies.map(({ item, depth }) => (
          <CommentCard
            key={item.id}
            comment={item}
            depth={depth}
            currentUserId={currentUser.id}
            onChanged={() => void reload()}
            onReplyCreated={() => void reload()}
            onOpen={() => navigate(`/comment/${item.id}`)}
          />
        ))}
      </section>
    </section>
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
  autoFocus = false,
}: {
  currentUser: UserSummary;
  onPosted: (tweet: Tweet) => void;
  autoFocus?: boolean;
}) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [posting, setPosting] = useState(false);
  const [visibility, setVisibility] = useState<TweetVisibility>("public");
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(content, setContent);
  const typeahead = useComposerTypeahead({
    text: content,
    onTextChange: setContent,
    fieldRef: fieldProps.ref,
  });
  const media = useMediaAttachment();
  const postLength = usePostLength();
  const remaining = postLength.limit - content.length;
  const canPost = (content.trim().length > 0 || media.mediaUrls.length > 0) && remaining >= 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canPost || media.uploading) {
      return;
    }

    setPosting(true);
    setError("");
    try {
      const tweet = await createTweet(
        content.trim(),
        media.mediaUrls,
        media.mediaAlts,
        undefined,
        visibility,
      );
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
      <div className="composer-scroll">
        <Avatar user={currentUser} />
        <div className="composer-body">
          <div className="composer-input">
            <ComposerHighlight text={content} limit={postLength.limit} />
            <textarea
              {...fieldProps}
              onKeyDown={typeahead.onKeyDown}
              value={content}
              rows={1}
              placeholder="What is happening?"
              aria-label="Tweet content"
              autoFocus={autoFocus}
            />
            {typeahead.menu}
          </div>
          <MediaPreview attachment={media} />
          {error ? <p className="form-error">{error}</p> : null}
        </div>
      </div>
      {/* Siblings of the scrolling text row, not children of it, so the modal
          can pin them under a draft that has grown past the dialog's height. */}
      {remaining < 0 ? <ComposerLimitNotice postLength={postLength} /> : null}
      <div className="composer-visibility">
        <VisibilityPicker value={visibility} onChange={setVisibility} disabled={posting} />
      </div>
      <div className="composer-actions">
        <div className="composer-tools">
          <EmojiPicker onSelect={insertEmoji} />
          <MediaButton attachment={media} />
        </div>
        <span className={counterClass(remaining)}>{remaining}</span>
        <button className="primary-button compact" disabled={posting || media.uploading || !canPost}>
          {posting ? "Posting..." : "Post"}
        </button>
      </div>
    </form>
  );
}

/**
 * Follow the author from the post itself, the way Bluesky does.
 *
 * The Relevant people panel already offers this, but it lives in the discovery
 * column, which is gone below 1040px and on mobile -- so on the widths where a
 * post is most likely to be read, there was no way to follow whoever wrote it
 * without first opening their profile.
 *
 * Follow state is not on a tweet's author (UserSummary carries no
 * `is_following`), so it comes from the author's profile. `refreshToken` is
 * what keeps this and the panel agreeing: either one bumps it after a change,
 * and both re-read.
 */
function AuthorFollowButton({
  username,
  refreshToken,
  onChanged,
}: {
  username: string;
  refreshToken: number;
  onChanged: () => void;
}) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getUserProfile(username)
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      // A profile that will not load simply leaves the button out, rather than
      // putting an error above a post the reader came here to read.
      .catch(() => {
        if (!cancelled) setProfile(null);
      });
    return () => {
      cancelled = true;
    };
  }, [username, refreshToken]);

  if (!profile || profile.is_current_user) {
    return null;
  }

  async function toggleFollow(target: UserProfile) {
    setBusy(true);
    // Optimistic: the button is the only feedback there is, so it has to move
    // when pressed. Rolled back below if the call fails.
    setProfile({ ...target, is_following: !target.is_following });
    try {
      if (target.is_following) {
        await unfollowUser(target.id);
      } else {
        await followUser(target.id);
      }
      onChanged();
    } catch {
      setProfile(target);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className="follow-button detail-follow"
      onClick={() => void toggleFollow(profile)}
      disabled={busy}
      aria-pressed={profile.is_following}
    >
      <UserPlus size={15} aria-hidden="true" />
      {profile.is_following ? "Following" : "Follow"}
    </button>
  );
}

function TweetDetail({
  tweet,
  onBack,
  onTweetPatch,
  scrollToPostId,
  currentUserId,
  onDeleted,
  refreshToken,
  onDiscoveryChanged,
}: {
  tweet: Tweet;
  onBack: () => void;
  onTweetPatch: (tweetId: number, patch: Partial<Tweet>) => void;
  scrollToPostId?: number;
  currentUserId: number;
  onDeleted: () => void;
  refreshToken: number;
  onDiscoveryChanged: () => void;
}) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);
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
      // Muting hides an author's posts from timelines; it does not revoke
      // access to this one, which the reader deliberately opened. So the page
      // stays put -- unlike a feed card, which removes itself.
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function confirmBlock() {
    setModerating(true);
    setError("");
    try {
      await blockUser(tweet.author.id);
      // A blocked author's posts are not visible, so staying here would leave
      // the reader on a page that 404s the moment it reloads.
      onDeleted();
    } catch (err) {
      setError(getErrorMessage(err));
      setModerating(false);
    }
  }

  function startEditing() {
    setEditVisibility(tweet.visibility);
    setEditing(true);
  }

  async function saveEdit(content: string, mediaUrls: string[], mediaAlts: string[]) {
    setSavingEdit(true);
    try {
      const updated = await editTweet(tweet.id, content, mediaUrls, mediaAlts, editVisibility);
      onTweetPatch(tweet.id, {
        content: updated.content,
        media_urls: updated.media_urls,
        media_alts: updated.media_alts,
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
    const created = parseBackendDate(tweet.created_at);
    const time = new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(created);
    const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(created);
    return `${time} · ${date}`;
  }, [tweet.created_at]);

  const loadTweetComments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const loaded = await listComments(tweet.id);
      setComments(loaded);
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
  async function submitDetailComment(content: string, mediaUrls: string[], mediaAlts: string[]) {
    await createComment(tweet.id, content, mediaUrls, mediaAlts);
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
              <strong>{displayName(tweet.author)}</strong>
            </Link>
            <span>@{tweet.author.username}</span>
          </div>
          {/* Follow and the menu travel together at the end of the author row.
              A taken-down post offers neither: there is nothing left to edit,
              and nothing left to judge. */}
          {tweet.taken_down ? null : (
            <div className="detail-author-actions">
              <AuthorFollowButton
                username={tweet.author.username}
                refreshToken={refreshToken}
                onChanged={onDiscoveryChanged}
              />
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
            </div>
          )}
        </div>
        {tweet.taken_down ? (
          <div className="takedown-notice">
            <span>This post was removed for violating the rules.</span>
          </div>
        ) : (
          <PostBody text={tweet.content} enablePreview={tweet.media_urls.length === 0} />
        )}
        {editing ? (
          <PostEditor
            initialContent={tweet.content}
            initialMedia={tweet.media_urls}
            initialAlts={tweet.media_alts}
            saving={savingEdit}
            onSave={saveEdit}
            onCancel={() => setEditing(false)}
            visibility={editVisibility}
            onVisibilityChange={setEditVisibility}
          />
        ) : null}
        {tweet.media_urls.length > 0 ? (
          <MediaGallery urls={tweet.media_urls} alts={tweet.media_alts} />
        ) : null}
        {tweet.quoted_post ? <QuotedPostCard post={tweet.quoted_post} /> : null}
        {error ? <p className="tweet-error">{error}</p> : null}
        <div
          className={
            tweet.taken_down
              ? "detail-timestamp detail-timestamp--no-actions"
              : "detail-timestamp"
          }
        >
          <span>{displayDate}</span>
          {tweet.edited_at ? <span className="edited-tag">· edited</span> : null}
          <VisibilityBadge visibility={tweet.visibility} />
        </div>
        {tweet.taken_down ? null : (
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
        )}
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
            onOpen={() => navigate(`/comment/${item.id}`)}
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
          username={tweet.author.username}
          onClose={() => setReporting(false)}
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

  return (
    <section className="discovery-panel trending-panel" aria-labelledby="trending-title">
      <h2 id="trending-title">Trending</h2>
      {loading ? (
        <div className="loading-row small">
          <Loader2 className="spin" size={16} aria-hidden="true" />
        </div>
      ) : trends.length === 0 ? (
        <p className="trend-empty">Nothing trending yet.</p>
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

/**
 * Twitter-style "Relevant people" sidebar card for detail views: the post's
 * participants with their bio and a follow button, instead of the generic
 * suggestion list. Full profiles are fetched here because a UserSummary
 * carries neither the bio nor the follow state.
 */
function RelevantPeoplePanel({
  users,
  onChanged,
  refreshToken,
}: {
  users: UserSummary[];
  onChanged: () => void;
  /**
   * Bumped whenever a follow changes anywhere. The post's own follow button
   * shows the same people this panel does, so without re-reading here the two
   * would sit side by side disagreeing.
   */
  refreshToken: number;
}) {
  const [profiles, setProfiles] = useState<UserProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const usernamesKey = users.map((user) => user.username).join(",");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all(
      // A profile that fails to load (e.g. its account just got deleted)
      // silently drops out rather than blanking the whole panel.
      users.map((user) => getUserProfile(user.username).catch(() => null)),
    )
      .then((loaded) => {
        if (!cancelled) {
          setProfiles(loaded.filter((profile): profile is UserProfile => profile != null));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // users is rebuilt each render by callers; the usernames are the identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usernamesKey, refreshToken]);

  async function toggleFollow(profile: UserProfile) {
    try {
      if (profile.is_following) {
        await unfollowUser(profile.id);
      } else {
        await followUser(profile.id);
      }
      setProfiles((current) =>
        current.map((item) =>
          item.id === profile.id ? { ...item, is_following: !profile.is_following } : item,
        ),
      );
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  return (
    <section className="discovery-panel" aria-labelledby="relevant-title">
      <h2 id="relevant-title">Relevant people</h2>
      {error ? <p className="form-error">{error}</p> : null}
      <div className="user-list">
        {profiles.map((profile) => (
          <div className="user-row relevant-person" key={profile.id}>
            <Link
              to={`/${encodeURIComponent(profile.username)}`}
              className="author-link user-row-link"
              aria-label={`View profile of ${profile.username}`}
            >
              <Avatar user={profile} size="small" />
              <div className="user-copy">
                <strong>{displayName(profile)}</strong>
                <span>@{profile.username}</span>
                {profile.bio ? <p className="user-bio">{profile.bio}</p> : null}
              </div>
            </Link>
            {!profile.is_current_user ? (
              <button className="follow-button" onClick={() => void toggleFollow(profile)}>
                <UserPlus size={15} aria-hidden="true" />
                {profile.is_following ? "Following" : "Follow"}
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
