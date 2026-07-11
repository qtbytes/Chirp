import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KeyRound, Loader2, Mail, Monitor, Smartphone } from "lucide-react";
import {
  displayName,
  getUserProfile,
  listBlocked,
  listMuted,
  listSessions,
  logoutOtherSessions,
  revokeSession,
  unblockUser,
  unmuteUser,
} from "./api";
import type {
  BlockedUser,
  MutedUser,
  Session,
  UserProfile,
  UserSummary,
} from "./types";
import { Avatar, formatCompactDate, getErrorMessage } from "./components";
import { ChangeEmailModal } from "./ChangeEmailModal";
import { ChangePasswordModal } from "./ChangePasswordModal";

export function AccountView({ currentUser }: { currentUser: UserSummary }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [error, setError] = useState("");
  const [changingEmail, setChangingEmail] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setError("");
    getUserProfile(currentUser.username)
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [currentUser.username]);

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Account</h1>
        </div>
      </header>

      {error ? <div className="status-panel error">{error}</div> : null}

      {!profile && !error ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading account</span>
        </div>
      ) : null}

      {profile ? (
        <div className="settings-list">
          <div className="settings-row">
            <span className="settings-row-icon" aria-hidden="true">
              <Mail size={20} />
            </span>
            <div className="settings-row-copy">
              <strong>Email</strong>
              {profile.email ? (
                <span>{profile.email} · confirmed</span>
              ) : profile.pending_email ? (
                <span className="settings-warn">
                  {profile.pending_email} · awaiting confirmation
                </span>
              ) : (
                <span className="settings-warn">
                  Not set — required to reset a forgotten password
                </span>
              )}
            </div>
            <button className="outline-button" onClick={() => setChangingEmail(true)}>
              {profile.email || profile.pending_email ? "Change" : "Add"}
            </button>
          </div>

          <div className="settings-row">
            <span className="settings-row-icon" aria-hidden="true">
              <KeyRound size={20} />
            </span>
            <div className="settings-row-copy">
              <strong>Password</strong>
              <span>Changing it signs out every other device.</span>
            </div>
            <button className="outline-button" onClick={() => setChangingPassword(true)}>
              Change
            </button>
          </div>
        </div>
      ) : null}

      <SessionsSection />

      <BlockedSection />
      <MutedSection />

      {changingEmail && profile ? (
        <ChangeEmailModal
          profile={profile}
          onClose={() => setChangingEmail(false)}
          onChanged={(pendingEmail) =>
            setProfile({ ...profile, pending_email: pendingEmail })
          }
        />
      ) : null}

      {changingPassword ? (
        <ChangePasswordModal onClose={() => setChangingPassword(false)} />
      ) : null}
    </>
  );
}

/**
 * Best-effort, human-readable device label from a user-agent string. Deliberately
 * coarse: enough to tell "the phone in my pocket" from "a login I don't
 * recognise", not a full UA parser.
 */
function deviceLabel(userAgent: string | null): string {
  if (!userAgent) {
    return "Unknown device";
  }
  const ua = userAgent.toLowerCase();

  let browser = "Browser";
  if (ua.includes("edg/")) browser = "Edge";
  else if (ua.includes("chrome") && !ua.includes("chromium")) browser = "Chrome";
  else if (ua.includes("firefox")) browser = "Firefox";
  else if (ua.includes("safari") && !ua.includes("chrome")) browser = "Safari";
  else if (ua.includes("python") || ua.includes("curl") || ua.includes("httpx"))
    return userAgent.split(/[\s/]/)[0] || "Script";

  let os = "";
  if (ua.includes("iphone") || ua.includes("ipad")) os = "iOS";
  else if (ua.includes("android")) os = "Android";
  else if (ua.includes("windows")) os = "Windows";
  else if (ua.includes("mac os") || ua.includes("macintosh")) os = "macOS";
  else if (ua.includes("linux")) os = "Linux";

  return os ? `${browser} on ${os}` : browser;
}

function isMobile(userAgent: string | null): boolean {
  const ua = (userAgent ?? "").toLowerCase();
  return ua.includes("iphone") || ua.includes("ipad") || ua.includes("android");
}

function SessionsSection() {
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      setSessions(await listSessions());
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function revokeOne(id: string) {
    setBusyId(id);
    setError("");
    try {
      await revokeSession(id);
      setSessions((current) => current?.filter((s) => s.id !== id) ?? null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function logoutOthers() {
    setLoggingOut(true);
    setError("");
    try {
      await logoutOtherSessions();
      // Only the current session remains; reload to reflect the truth.
      await load();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoggingOut(false);
    }
  }

  const otherCount = sessions?.filter((s) => !s.current).length ?? 0;

  return (
    <section className="settings-section" aria-labelledby="sessions-heading">
      <div className="settings-section-head">
        <h2 id="sessions-heading">Active sessions</h2>
        {otherCount > 0 ? (
          <button
            className="outline-button"
            onClick={() => void logoutOthers()}
            disabled={loggingOut}
          >
            {loggingOut ? "Working…" : "Log out all others"}
          </button>
        ) : null}
      </div>

      <p className="form-hint settings-section-intro">
        Each device that logs in gets its own session. Sign out any you don&apos;t
        recognise; the others keep working.
      </p>

      {error ? <p className="form-error">{error}</p> : null}

      {sessions === null ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading sessions</span>
        </div>
      ) : (
        <ul className="session-list">
          {sessions.map((session) => (
            <li className="session-row" key={session.id}>
              <span className="session-icon" aria-hidden="true">
                {isMobile(session.user_agent) ? (
                  <Smartphone size={20} />
                ) : (
                  <Monitor size={20} />
                )}
              </span>
              <div className="session-copy">
                <strong>
                  {deviceLabel(session.user_agent)}
                  {session.current ? (
                    <span className="session-badge">This device</span>
                  ) : null}
                </strong>
                <span>
                  {session.ip ?? "unknown IP"} · active{" "}
                  {formatCompactDate(session.last_seen)}
                </span>
              </div>
              {session.current ? null : (
                <button
                  className="outline-button"
                  onClick={() => void revokeOne(session.id)}
                  disabled={busyId === session.id}
                >
                  {busyId === session.id ? "…" : "Log out"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BlockedSection() {
  const [items, setItems] = useState<BlockedUser[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async (nextCursor?: string | null, append = false) => {
    setError("");
    try {
      const page = await listBlocked(nextCursor);
      setItems((current) =>
        append && current ? [...current, ...page.items] : page.items,
      );
      setCursor(page.next_cursor);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function unblock(user: BlockedUser) {
    setBusyId(user.id);
    setError("");
    try {
      await unblockUser(user.id);
      setItems((current) => current?.filter((u) => u.id !== user.id) ?? null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="blocked-heading">
      <div className="settings-section-head">
        <h2 id="blocked-heading">Blocked accounts</h2>
      </div>
      <p className="form-hint settings-section-intro">
        Blocked accounts can&apos;t see your Tweets or interact with you, and you
        won&apos;t see theirs.
      </p>

      {error ? <p className="form-error">{error}</p> : null}

      {items === null ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : items.length === 0 ? (
        <p className="form-hint">You haven&apos;t blocked anyone.</p>
      ) : (
        <div className="user-list">
          {items.map((user) => (
            <div className="user-row" key={user.id}>
              <Link
                to={`/${encodeURIComponent(user.username)}`}
                className="author-link user-row-link"
              >
                <Avatar user={user} size="small" />
                <div className="user-copy">
                  <strong>{displayName(user)}</strong>
                  <span>@{user.username}</span>
                </div>
              </Link>
              <button
                className="outline-button"
                onClick={() => void unblock(user)}
                disabled={busyId === user.id}
              >
                {busyId === user.id ? "…" : "Unblock"}
              </button>
            </div>
          ))}
        </div>
      )}

      {cursor ? (
        <button className="load-more" onClick={() => void load(cursor, true)}>
          Load more
        </button>
      ) : null}
    </section>
  );
}

function MutedSection() {
  const [items, setItems] = useState<MutedUser[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async (nextCursor?: string | null, append = false) => {
    setError("");
    try {
      const page = await listMuted(nextCursor);
      setItems((current) =>
        append && current ? [...current, ...page.items] : page.items,
      );
      setCursor(page.next_cursor);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function unmute(user: MutedUser) {
    setBusyId(user.id);
    setError("");
    try {
      await unmuteUser(user.id);
      setItems((current) => current?.filter((u) => u.id !== user.id) ?? null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="muted-heading">
      <div className="settings-section-head">
        <h2 id="muted-heading">Muted accounts</h2>
      </div>
      <p className="form-hint settings-section-intro">
        You won&apos;t see muted accounts in your timeline or notifications. They
        can still follow you and see your Tweets, and they aren&apos;t told.
      </p>

      {error ? <p className="form-error">{error}</p> : null}

      {items === null ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : items.length === 0 ? (
        <p className="form-hint">You haven&apos;t muted anyone.</p>
      ) : (
        <div className="user-list">
          {items.map((user) => (
            <div className="user-row" key={user.id}>
              <Link
                to={`/${encodeURIComponent(user.username)}`}
                className="author-link user-row-link"
              >
                <Avatar user={user} size="small" />
                <div className="user-copy">
                  <strong>{displayName(user)}</strong>
                  <span>@{user.username}</span>
                </div>
              </Link>
              <button
                className="outline-button"
                onClick={() => void unmute(user)}
                disabled={busyId === user.id}
              >
                {busyId === user.id ? "…" : "Unmute"}
              </button>
            </div>
          ))}
        </div>
      )}

      {cursor ? (
        <button className="load-more" onClick={() => void load(cursor, true)}>
          Load more
        </button>
      ) : null}
    </section>
  );
}
