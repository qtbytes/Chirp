export type UserSummary = {
  id: number;
  username: string;
  display_name: string | null;
  created_at: string;
  avatar_url: string | null;
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

export type Tweet = {
  id: number;
  content: string;
  media_urls: string[];
  created_at: string;
  edited_at: string | null;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  liked_by_me: boolean;
  quoted_post: QuotedPost | null;
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
  liked_by_me: boolean;
};

export type Comment = {
  id: number;
  tweet_id: number;
  parent_comment_id: number | null;
  content: string;
  media_urls: string[];
  created_at: string;
  edited_at: string | null;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
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
  tweet_count: number;
  is_following: boolean;
  is_current_user: boolean;
  /** Whether you have blocked this account. */
  is_blocked: boolean;
  /** Whether you have muted this account. One-directional; they aren't told. */
  is_muted: boolean;
  /** Confirmed address. Null on anyone else's profile, and until confirmed. */
  email: string | null;
  /** Claimed but unconfirmed. Null on anyone else's profile. */
  pending_email: string | null;
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

export type NotificationType =
  | "like"
  | "retweet"
  | "comment"
  | "reply"
  | "follow";

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

export type ReplyWithParent = {
  comment: Comment;
  parent_tweet: Tweet;
};

export type ProfileRepliesPage = {
  items: ReplyWithParent[];
  next_cursor: string | null;
};
