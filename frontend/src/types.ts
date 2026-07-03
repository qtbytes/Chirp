export type UserSummary = {
  id: number;
  username: string;
  created_at: string;
};

export type UserDiscovery = UserSummary & {
  is_following: boolean;
  is_current_user: boolean;
};

export type Tweet = {
  id: number;
  content: string;
  created_at: string;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  liked_by_me: boolean;
};

export type LikeToggleResult = {
  tweet_id: number;
  liked: boolean;
  like_count: number;
};

export type Comment = {
  id: number;
  tweet_id: number;
  parent_comment_id: number | null;
  content: string;
  created_at: string;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
};

export type TimelinePage = {
  items: Tweet[];
  next_cursor: string | null;
  strategy: "read" | "write" | "for_you";
};

export type TimelineKind = "for-you" | "following";
