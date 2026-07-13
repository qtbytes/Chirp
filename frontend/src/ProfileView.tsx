import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Ban,
  Calendar,
  Loader2,
  MoreHorizontal,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  ApiError,
  blockUser,
  displayName,
  followUser,
  getUserProfile,
  getUserReplies,
  getUserTweets,
  muteUser,
  recordPostViews,
  unblockUser,
  unfollowUser,
  unmuteUser,
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
  ConfirmDialog,
  TweetCard,
  getErrorMessage,
  parseBackendDate,
} from "./components";
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
  const [blockBusy, setBlockBusy] = useState(false);
  const [muteBusy, setMuteBusy] = useState(false);
  const [confirmingBlock, setConfirmingBlock] = useState(false);
  const [editing, setEditing] = useState(false);

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
        void recordPostViews(page.items.map((t) => t.id));
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
        const ids = page.items.flatMap((item) => [item.parent_tweet.id, item.comment.id]);
        void recordPostViews(ids);
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

  async function toggleBlock() {
    if (!profile || blockBusy) {
      return;
    }
    setBlockBusy(true);
    setProfileError("");
    try {
      if (profile.is_blocked) {
        await unblockUser(profile.id);
      } else {
        await blockUser(profile.id);
      }
      // Visibility changed in both directions; refetch the profile (counts,
      // is_following, is_blocked) and the feed, which is now empty or restored.
      const updated = await getUserProfile(username);
      setProfile(updated);
      if (activeTab === "tweets") {
        void loadTweets();
      } else {
        void loadReplies();
      }
    } catch (err) {
      setProfileError(getErrorMessage(err));
    } finally {
      setBlockBusy(false);
      setConfirmingBlock(false);
    }
  }

  async function toggleMute() {
    if (!profile || muteBusy) {
      return;
    }
    setMuteBusy(true);
    setProfileError("");
    try {
      if (profile.is_muted) {
        await unmuteUser(profile.id);
      } else {
        await muteUser(profile.id);
      }
      // A mute only changes what's hidden elsewhere (timeline, notifications);
      // this profile's own tweets stay visible, so just flip the flag.
      setProfile({ ...profile, is_muted: !profile.is_muted });
    } catch (err) {
      setProfileError(getErrorMessage(err));
    } finally {
      setMuteBusy(false);
    }
  }

  // After muting or blocking the profile's owner from one of their post cards,
  // resync from the server: a block flips the profile to its blocked state and
  // empties the feed, while a mute leaves the posts in place but updates the
  // header's Mute/Unmute control.
  async function refreshAfterModeration() {
    try {
      setProfile(await getUserProfile(username));
    } catch (err) {
      setProfileError(getErrorMessage(err));
    }
    if (activeTab === "tweets") {
      void loadTweets();
    } else {
      void loadReplies();
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
          {profile.is_deleted ? null : profile.is_current_user ? (
            <div className="profile-actions">
              <button className="outline-button" onClick={() => setEditing(true)}>
                Edit profile
              </button>
            </div>
          ) : profile.is_blocked ? (
            <div className="profile-actions">
              <button
                className="blocked-toggle"
                onClick={() => void toggleBlock()}
                disabled={blockBusy}
                title="Unblock"
              >
                <span className="blocked-toggle-default">Blocked</span>
                <span className="blocked-toggle-hover">Unblock</span>
              </button>
            </div>
          ) : (
            <div className="profile-actions">
              <ProfileOverflowMenu
                isMuted={profile.is_muted}
                muteBusy={muteBusy}
                blockBusy={blockBusy}
                onToggleMute={() => void toggleMute()}
                onBlock={() => setConfirmingBlock(true)}
              />
              <button
                className={profile.is_following ? "outline-button following" : "primary-button compact"}
                onClick={() => void toggleFollow()}
                disabled={followBusy}
              >
                {profile.is_following ? "Following" : "Follow"}
              </button>
            </div>
          )}
        </div>
        <h1 className="profile-name">{displayName(profile)}</h1>
        <p className="profile-handle">@{profile.username}</p>
        {profile.is_deleted ? (
          <p className="profile-bio profile-deleted-note">
            This account has been deleted.
          </p>
        ) : profile.bio ? (
          <p className="profile-bio">{profile.bio}</p>
        ) : null}
        <p className="profile-meta">
          <Calendar size={16} aria-hidden="true" />
          <span>Joined {joinedDate}</span>
        </p>
        <p className="profile-stats">
          <Link to={`/${encodeURIComponent(username)}/following`}>
            <strong>{profile.following_count}</strong> Following
          </Link>
          <Link to={`/${encodeURIComponent(username)}/followers`}>
            <strong>{profile.follower_count}</strong> Followers
          </Link>
        </p>
        {profileError ? <p className="form-error">{profileError}</p> : null}
      </header>

      {!profile.is_current_user && profile.is_muted && !profile.is_blocked ? (
        <div className="muted-notice">
          You have muted posts from this account.{" "}
          <button
            className="text-button inline"
            onClick={() => void toggleMute()}
            disabled={muteBusy}
          >
            Unmute
          </button>
        </div>
      ) : null}

      {profile.is_blocked ? null : (
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
      )}

      {feedError ? <div className="status-panel error">{feedError}</div> : null}

      {profile.is_blocked ? (
        <div className="blocked-notice">
          <h2 className="blocked-notice-title">
            @{profile.username} is blocked
          </h2>
          <p className="blocked-notice-text">
            You can&apos;t see their Tweets, and they can&apos;t see yours or
            interact with you.
          </p>
        </div>
      ) : activeTab === "tweets" ? (
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
                onAuthorMuted={() => void refreshAfterModeration()}
                onAuthorBlocked={() => void refreshAfterModeration()}
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
                  onAuthorMuted={() => void loadReplies()}
                  onAuthorBlocked={() => void loadReplies()}
                />
                <CommentCard
                  comment={item.comment}
                  currentUserId={currentUser.id}
                  onChanged={() => void loadReplies()}
                  onReplyCreated={() => void loadReplies()}
                  onOpen={() =>
                    navigate(`/tweet/${item.comment.tweet_id}`, {
                      state: { scrollToPostId: item.comment.id },
                    })
                  }
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

      {!profile.is_blocked && loadingFeed ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : null}

      {confirmingBlock ? (
        <ConfirmDialog
          title={`Block @${profile.username}?`}
          message="They won't be able to follow you or see your Tweets, and you won't see theirs. Any follow between you is removed."
          confirmLabel="Block"
          busyLabel="Blocking…"
          busy={blockBusy}
          onConfirm={() => void toggleBlock()}
          onCancel={() => setConfirmingBlock(false)}
        />
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

    </section>
  );
}

function ProfileOverflowMenu({
  isMuted,
  muteBusy,
  blockBusy,
  onToggleMute,
  onBlock,
}: {
  isMuted: boolean;
  muteBusy: boolean;
  blockBusy: boolean;
  onToggleMute: () => void;
  onBlock: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onDocMouseDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  return (
    <div className="profile-menu" ref={ref}>
      <button
        type="button"
        className="outline-icon-button"
        onClick={() => setOpen((value) => !value)}
        aria-label="More options"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={muteBusy || blockBusy}
      >
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
      {open ? (
        <div className="post-menu-dropdown" role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onToggleMute();
            }}
          >
            {isMuted ? (
              <Volume2 size={16} aria-hidden="true" />
            ) : (
              <VolumeX size={16} aria-hidden="true" />
            )}
            <span>{isMuted ? "Unmute" : "Mute"}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="danger-menu-item"
            onClick={() => {
              setOpen(false);
              onBlock();
            }}
          >
            <Ban size={16} aria-hidden="true" />
            <span>Block</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}
