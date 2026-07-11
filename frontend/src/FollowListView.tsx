import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, UserPlus } from "lucide-react";
import {
  ApiError,
  displayName,
  followUser,
  getFollowers,
  getFollowing,
  unfollowUser,
} from "./api";
import type { UserDiscovery } from "./types";
import { Avatar, getErrorMessage } from "./components";

type Tab = "followers" | "following";

export function FollowListView() {
  const { username = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const tab: Tab = location.pathname.endsWith("/following") ? "following" : "followers";

  const [items, setItems] = useState<UserDiscovery[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(
    async (nextCursor?: string | null, append = false) => {
      setLoading(true);
      setError("");
      try {
        const fetcher = tab === "following" ? getFollowing : getFollowers;
        const page = await fetcher(username, nextCursor);
        setItems((current) => (append ? [...current, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(getErrorMessage(err));
        }
      } finally {
        setLoading(false);
      }
    },
    [tab, username],
  );

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setNotFound(false);
    void load();
  }, [load]);

  async function toggleFollow(user: UserDiscovery) {
    // Optimistic: flip the row, roll back if the request fails.
    setItems((current) =>
      current.map((u) => (u.id === user.id ? { ...u, is_following: !u.is_following } : u)),
    );
    try {
      if (user.is_following) {
        await unfollowUser(user.id);
      } else {
        await followUser(user.id);
      }
    } catch (err) {
      setItems((current) =>
        current.map((u) =>
          u.id === user.id ? { ...u, is_following: user.is_following } : u,
        ),
      );
      setError(getErrorMessage(err));
    }
  }

  if (notFound) {
    return (
      <section className="follow-list-view">
        <div className="detail-toolbar">
          <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h2>Profile</h2>
        </div>
        <div className="status-panel">
          <strong>This account doesn&apos;t exist</strong>
          <p>Try searching for another.</p>
        </div>
      </section>
    );
  }

  const empty =
    tab === "followers"
      ? "No followers yet."
      : `@${username} isn't following anyone yet.`;

  return (
    <section className="follow-list-view" aria-label={`${tab} of ${username}`}>
      <div className="detail-toolbar">
        <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <div className="profile-toolbar-copy">
          <h2>@{username}</h2>
        </div>
      </div>

      <div className="tab-list" role="tablist" aria-label="Follow lists">
        <Link
          to={`/${encodeURIComponent(username)}/followers`}
          className={tab === "followers" ? "tab active" : "tab"}
          role="tab"
          aria-selected={tab === "followers"}
        >
          Followers
        </Link>
        <Link
          to={`/${encodeURIComponent(username)}/following`}
          className={tab === "following" ? "tab active" : "tab"}
          role="tab"
          aria-selected={tab === "following"}
        >
          Following
        </Link>
      </div>

      {error ? <div className="status-panel error">{error}</div> : null}

      {!loading && items.length === 0 && !error ? (
        <div className="status-panel">{empty}</div>
      ) : null}

      <div className="user-list">
        {items.map((user) => (
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
            {user.is_current_user ? null : (
              <button className="follow-button" onClick={() => void toggleFollow(user)}>
                <UserPlus size={15} aria-hidden="true" />
                {user.is_following ? "Following" : "Follow"}
              </button>
            )}
          </div>
        ))}
      </div>

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
          <span>Loading</span>
        </div>
      ) : null}
    </section>
  );
}
