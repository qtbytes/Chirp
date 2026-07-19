export type UserSummary = {
  id: number;
  username: string;
  display_name: string | null;
  created_at: string;
  avatar_url: string | null;
  /** True once the account is deleted; the UI renders it as a tombstone. */
  is_deleted?: boolean;
  /**
   * Present only on the caller's own record (register/login/me); gates the
   * moderation UI. Never sent on tweet authors, so the roster stays private.
   */
  is_moderator?: boolean;
};

export type UserDiscovery = UserSummary & {
  is_following: boolean;
  is_current_user: boolean;
};

export type FollowListPage = {
  items: UserDiscovery[];
  next_cursor: string | null;
};

export type QuotedPost = {
  id: number;
  content: string;
  media_urls: string[];
  /** Per-image alt text, parallel to media_urls ("" = none). */
  media_alts: string[];
  created_at: string;
  author: UserSummary;
};

export type LinkPreview = {
  url: string;
  title: string;
  description: string | null;
  image: string | null;
  site_name: string | null;
};

/** Who can see a tweet. Set at post time; a reply inherits its thread's. */
export type TweetVisibility = "public" | "followers" | "private";

export type Tweet = {
  id: number;
  content: string;
  media_urls: string[];
  /** Per-image alt text, parallel to media_urls ("" = none). */
  media_alts: string[];
  created_at: string;
  edited_at: string | null;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  view_count: number;
  liked_by_me: boolean;
  quoted_post: QuotedPost | null;
  visibility: TweetVisibility;
  /**
   * True when a moderator removed this post. The detail endpoint still
   * answers -- content and media arrive masked -- so the thread stays
   * reachable and the UI shows a tombstone instead of the body.
   */
  taken_down?: boolean;
};

export type LikeToggleResult = {
  tweet_id: number;
  liked: boolean;
  like_count: number;
};

export type TweetStats = {
  id: number;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  view_count: number;
  liked_by_me: boolean;
};

export type CommentLikeToggleResult = {
  comment_id: number;
  liked: boolean;
  like_count: number;
};

export type CommentStats = {
  id: number;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  view_count: number;
  liked_by_me: boolean;
};

export type Comment = {
  id: number;
  tweet_id: number;
  parent_comment_id: number | null;
  content: string;
  media_urls: string[];
  /** Per-image alt text, parallel to media_urls ("" = none). */
  media_alts: string[];
  created_at: string;
  edited_at: string | null;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  view_count: number;
  liked_by_me: boolean;
  quoted_post: QuotedPost | null;
};

export type TimelinePage = {
  items: Tweet[];
  next_cursor: string | null;
  strategy: "read" | "write" | "for_you";
};

export type TimelineKind = "for-you" | "following";

export type UserProfile = {
  id: number;
  username: string;
  display_name: string | null;
  bio: string | null;
  avatar_url: string | null;
  created_at: string;
  follower_count: number;
  following_count: number;
  /** Everything they've authored: tweets, replies, and quotes/retweets. */
  post_count: number;
  is_following: boolean;
  is_current_user: boolean;
  /** Whether this profile belongs to a deleted (tombstoned) account. */
  is_deleted: boolean;
  /** Whether you have blocked this account. */
  is_blocked: boolean;
  /** Whether you have muted this account. One-directional; they aren't told. */
  is_muted: boolean;
  /** Confirmed address. Null on anyone else's profile, and until confirmed. */
  email: string | null;
  /** Claimed but unconfirmed. Null on anyone else's profile. */
  pending_email: string | null;
  /** Who may DM you. Null on anyone else's profile. */
  dm_policy: DmPolicy | null;
};

export type BlockedUser = UserSummary & {
  blocked_at: string;
};

export type BlockListPage = {
  items: BlockedUser[];
  next_cursor: string | null;
};

export type ReportReason =
  | "spam"
  | "abuse"
  | "hate"
  | "violence"
  | "sensitive"
  | "misinformation"
  | "other";

export type Report = {
  id: number;
  post_id: number;
  reason: ReportReason;
  created_at: string;
};

export type ReportStatus = "open" | "dismissed" | "actioned";

/** One reporter's complaint, as the moderation queue shows it. */
export type ModerationReport = {
  id: number;
  reporter: UserSummary;
  reason: ReportReason;
  details: string | null;
  created_at: string;
  status: ReportStatus;
};

/** The reported post, unmasked (a moderator judges the evidence). */
export type ModerationPost = {
  id: number;
  content: string;
  media_urls: string[];
  created_at: string;
  author: UserSummary;
  is_reply: boolean;
  thread_id: number;
  taken_down: boolean;
};

export type ModerationQueueItem = {
  post: ModerationPost;
  report_count: number;
  latest_report_at: string;
  reports: ModerationReport[];
};

export type ModerationQueuePage = {
  items: ModerationQueueItem[];
  next_cursor: string | null;
};

export type ModerationAction = {
  post_id: number;
  taken_down: boolean;
  resolved_reports: number;
};

export type MutedUser = UserSummary & {
  muted_at: string;
};

export type MuteListPage = {
  items: MutedUser[];
  next_cursor: string | null;
};

export type Session = {
  /** Opaque handle (sha256 of the session id), safe to pass back for revocation. */
  id: string;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen: string;
  /** The session making the request. Never offered a revoke button. */
  current: boolean;
};

export type ProfileTweetsPage = {
  items: Tweet[];
  next_cursor: string | null;
};

/**
 * A content-search hit. Shaped like a Tweet so a top-level result renders in a
 * TweetCard, plus `is_reply`/`thread_id` so a reply hit can link into its thread.
 */
export type SearchPost = Tweet & {
  is_reply: boolean;
  thread_id: number;
};

export type SearchSort = "relevance" | "recent";

export type SearchPage = {
  items: SearchPost[];
  next_cursor: string | null;
};

/** A hot hashtag for the Trending panel. */
export type TrendingHashtag = {
  tag: string;
  post_count: number;
};

export type NotificationType =
  | "like"
  | "retweet"
  | "comment"
  | "reply"
  | "follow"
  | "mention";

export type Notification = {
  id: number;
  type: NotificationType;
  actor: UserSummary;
  tweet_id: number | null;
  comment_id: number | null;
  preview: string | null;
  is_read: boolean;
  created_at: string;
};

export type NotificationPage = {
  items: Notification[];
  next_cursor: string | null;
};

/**
 * A profile feed item that may be a tweet or a reply (the Media and Likes
 * tabs mix both). Tweet-shaped so a top-level item renders in a TweetCard;
 * a reply carries `thread_id`/`parent_comment_id` so it can render as a
 * comment and link into its thread.
 */
export type ProfilePost = Tweet & {
  is_reply: boolean;
  thread_id: number;
  parent_comment_id: number | null;
};

export type ProfileMediaPage = {
  items: ProfilePost[];
  next_cursor: string | null;
};

export type ProfileLikesPage = {
  items: ProfilePost[];
  next_cursor: string | null;
};

/** Who may open a DM conversation with you. Like reply controls. */
export type DmPolicy = "everyone" | "following" | "none";

export type DmMessage = {
  id: number;
  sender_id: number;
  content: string;
  created_at: string;
};

export type DmConversation = {
  id: number;
  other_user: UserSummary;
  last_message: DmMessage | null;
  unread_count: number;
  /** Your own mute; the other participant never sees it. */
  muted: boolean;
  /** Whether you've blocked them (the row menu offers Unblock instead). */
  blocked: boolean;
};

export type DmConversationPage = {
  items: DmConversation[];
  next_cursor: string | null;
};

/**
 * One chat as the conversation view needs it. `messages` come newest first;
 * `next_cursor` pages further back. When `can_send` is false,
 * `cannot_send_reason` says why: "policy" (their setting refuses you) or
 * "await_reply" (your one opener is out; wait for them to answer).
 */
export type DmChat = {
  other_user: UserSummary;
  messages: DmMessage[];
  next_cursor: string | null;
  can_send: boolean;
  /**
   * "policy": their setting refuses you; "await_reply": your one opener is
   * out; "you_blocked": you blocked them (unblock to resume);
   * "blocked_you": they blocked you. Either block leaves the history
   * readable; only sending ends.
   */
  cannot_send_reason:
    | "policy"
    | "await_reply"
    | "you_blocked"
    | "blocked_you"
    | null;
  muted: boolean;
  /** Whether you've blocked them (the chat menu offers Unblock instead). */
  blocked: boolean;
};

export type ReplyWithParent = {
  comment: Comment;
  parent_tweet: Tweet;
};

export type ProfileRepliesPage = {
  items: ReplyWithParent[];
  next_cursor: string | null;
};
