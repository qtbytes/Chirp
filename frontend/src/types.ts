export type UserSummary = {
  id: number;
  username: string;
  created_at: string;
  avatar_url: string | null;
};

export type UserDiscovery = UserSummary & {
  is_following: boolean;
  is_current_user: boolean;
};

export type Tweet = {
  id: number;
  content: string;
  media_url: string | null;
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

export type TweetStats = {
  id: number;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  liked_by_me: boolean;
};

export type RetweetResult = {
  tweet_id: number;
  retweeted: boolean;
  created: boolean;
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
  media_url: string | null;
  created_at: string;
  author: UserSummary;
  like_count: number;
  comment_count: number;
  retweet_count: number;
  liked_by_me: boolean;
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
  bio: string | null;
  avatar_url: string | null;
  created_at: string;
  follower_count: number;
  following_count: number;
  tweet_count: number;
  is_following: boolean;
  is_current_user: boolean;
};

export type ProfileTweetsPage = {
  items: Tweet[];
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
