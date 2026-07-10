import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Calendar, Loader2 } from "lucide-react";
import {
  ApiError,
  displayName,
  followUser,
  getUserProfile,
  getUserReplies,
  getUserTweets,
  unfollowUser,
} from "./api";
import type {
  ReplyWithParent,
  Tweet,
  UserProfile,
  UserSummary,
} from "./types";
import {
  Avatar,
  CommentCard,
  TweetCard,
  getErrorMessage,
  parseBackendDate,
} from "./components";
import { ChangePasswordModal } from "./ChangePasswordModal";
import { EditProfileModal } from "./EditProfileModal";

export function ProfileView({
  currentUser,
  onCurrentUserChange,
}: {
  currentUser: UserSummary;
  onCurrentUserChange: (user: UserSummary) => void;
}) {
  const { username = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab: "tweets" | "replies" = location.pathname.endsWith("/replies")
    ? "replies"
    : "tweets";

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [followBusy, setFollowBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  const [tweets, setTweets] = useState<Tweet[]>([]);
  const [tweetsCursor, setTweetsCursor] = useState<string | null>(null);
  const [replies, setReplies] = useState<ReplyWithParent[]>([]);
  const [repliesCursor, setRepliesCursor] = useState<string | null>(null);
  const [loadingFeed, setLoadingFeed] = useState(false);
  const [feedError, setFeedError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setNotFound(false);
    setProfileError("");
    getUserProfile(username)
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setProfileError(getErrorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [username]);

  const loadTweets = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");
      try {
        const page = await getUserTweets(username, cursor);
        setTweets((current) => (append ? [...current, ...page.items] : page.items));
        setTweetsCursor(page.next_cursor);
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [username],
  );

  const loadReplies = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");
      try {
        const page = await getUserReplies(username, cursor);
        setReplies((current) => (append ? [...current, ...page.items] : page.items));
        setRepliesCursor(page.next_cursor);
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [username],
  );

  useEffect(() => {
    setTweets([]);
    setReplies([]);
    if (notFound) {
      return;
    }
    if (activeTab === "tweets") {
      void loadTweets();
    } else {
      void loadReplies();
    }
  }, [activeTab, loadTweets, loadReplies, notFound]);

  const patchTweet = useCallback((tweetId: number, patch: Partial<Tweet>) => {
    setTweets((current) =>
      current.map((tweet) => (tweet.id === tweetId ? { ...tweet, ...patch } : tweet)),
    );
  }, []);

  const patchReplyParent = useCallback((postId: number, patch: Partial<Tweet>) => {
    setReplies((current) =>
      current.map((item) =>
        item.parent_tweet.id === postId
          ? { ...item, parent_tweet: { ...item.parent_tweet, ...patch } }
          : item,
      ),
    );
  }, []);

  async function toggleFollow() {
    if (!profile || followBusy) {
      return;
    }
    setFollowBusy(true);
    try {
      if (profile.is_following) {
        await unfollowUser(profile.id);
        setProfile({
          ...profile,
          is_following: false,
          follower_count: Math.max(0, profile.follower_count - 1),
        });
      } else {
        await followUser(profile.id);
        setProfile({
          ...profile,
          is_following: true,
          follower_count: profile.follower_count + 1,
        });
      }
    } catch (err) {
      setProfileError(getErrorMessage(err));
    } finally {
      setFollowBusy(false);
    }
  }

  if (notFound) {
    return (
      <section className="profile-view">
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

  if (!profile) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading profile</span>
      </div>
    );
  }

  const joinedDate = new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
  }).format(parseBackendDate(profile.created_at));

  return (
    <section className="profile-view" aria-label={`Profile of ${profile.username}`}>
      <div className="detail-toolbar">
        <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <div className="profile-toolbar-copy">
          <h2>{displayName(profile)}</h2>
          <span>{profile.tweet_count} Tweets</span>
        </div>
      </div>

      <div className="profile-banner" aria-hidden="true" />
      <header className="profile-header">
        <div className="profile-header-top">
          <Avatar user={profile} size="large" />
          {profile.is_current_user ? (
            <div className="profile-actions">
              <button className="outline-button" onClick={() => setChangingPassword(true)}>
                Change password
              </button>
              <button className="outline-button" onClick={() => setEditing(true)}>
                Edit profile
              </button>
            </div>
          ) : (
            <button
              className={profile.is_following ? "outline-button following" : "primary-button compact"}
              onClick={() => void toggleFollow()}
              disabled={followBusy}
            >
              {profile.is_following ? "Following" : "Follow"}
            </button>
          )}
        </div>
        <h1 className="profile-name">{displayName(profile)}</h1>
        <p className="profile-handle">@{profile.username}</p>
        {profile.bio ? <p className="profile-bio">{profile.bio}</p> : null}
        <p className="profile-meta">
          <Calendar size={16} aria-hidden="true" />
          <span>Joined {joinedDate}</span>
        </p>
        <p className="profile-stats">
          <span>
            <strong>{profile.following_count}</strong> Following
          </span>
          <span>
            <strong>{profile.follower_count}</strong> Followers
          </span>
        </p>
        {profileError ? <p className="form-error">{profileError}</p> : null}
      </header>

      <div className="tab-list" role="tablist" aria-label="Profile content">
        <Link
          to={`/${encodeURIComponent(username)}`}
          className={activeTab === "tweets" ? "tab active" : "tab"}
          role="tab"
          aria-selected={activeTab === "tweets"}
        >
          Tweets
        </Link>
        <Link
          to={`/${encodeURIComponent(username)}/replies`}
          className={activeTab === "replies" ? "tab active" : "tab"}
          role="tab"
          aria-selected={activeTab === "replies"}
        >
          Replies
        </Link>
      </div>

      {feedError ? <div className="status-panel error">{feedError}</div> : null}

      {activeTab === "tweets" ? (
        <>
          {!loadingFeed && tweets.length === 0 && !feedError ? (
            <div className="status-panel">No tweets yet.</div>
          ) : null}
          <section className="tweet-list">
            {tweets.map((tweet) => (
              <TweetCard
                key={tweet.id}
                tweet={tweet}
                onOpen={() => navigate(`/tweet/${tweet.id}`)}
                onTweetPatch={patchTweet}
                currentUserId={currentUser.id}
                onDeleted={(id) =>
                  setTweets((current) => current.filter((item) => item.id !== id))
                }
              />
            ))}
          </section>
          {tweetsCursor ? (
            <button
              className="load-more"
              onClick={() => void loadTweets(tweetsCursor, true)}
              disabled={loadingFeed}
            >
              Load more
            </button>
          ) : null}
        </>
      ) : (
        <>
          {!loadingFeed && replies.length === 0 && !feedError ? (
            <div className="status-panel">No replies yet.</div>
          ) : null}
          <section className="tweet-list">
            {replies.map((item) => (
              <div className="profile-reply" key={item.comment.id}>
                <TweetCard
                  tweet={item.parent_tweet}
                  onOpen={() =>
                    navigate(`/tweet/${item.comment.tweet_id}`, {
                      state: { scrollToPostId: item.parent_tweet.id },
                    })
                  }
                  onTweetPatch={patchReplyParent}
                  currentUserId={currentUser.id}
                  onDeleted={() => void loadReplies()}
                />
                <CommentCard
                  comment={item.comment}
                  currentUserId={currentUser.id}
                  onChanged={() => void loadReplies()}
                  onReplyCreated={() => void loadReplies()}
                />
              </div>
            ))}
          </section>
          {repliesCursor ? (
            <button
              className="load-more"
              onClick={() => void loadReplies(repliesCursor, true)}
              disabled={loadingFeed}
            >
              Load more
            </button>
          ) : null}
        </>
      )}

      {loadingFeed ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : null}

      {editing && profile.is_current_user ? (
        <EditProfileModal
          profile={profile}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setProfile(updated);
            if (updated.id === currentUser.id) {
              onCurrentUserChange({
                ...currentUser,
                display_name: updated.display_name,
                avatar_url: updated.avatar_url,
              });
            }
          }}
        />
      ) : null}

      {changingPassword && profile.is_current_user ? (
        <ChangePasswordModal onClose={() => setChangingPassword(false)} />
      ) : null}
    </section>
  );
}
