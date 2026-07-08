import type {
  Comment,
  CommentLikeToggleResult,
  CommentStats,
  LikeToggleResult,
  Notification,
  ProfileRepliesPage,
  ProfileTweetsPage,
  RetweetResult,
  TimelineKind,
  TimelinePage,
  Tweet,
  TweetStats,
  UserDiscovery,
  UserProfile,
  UserSummary,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

const BACKEND_ORIGIN = new URL(API_BASE_URL).origin;

export function resolveMediaUrl(path: string | null): string | null {
  if (!path) {
    return null;
  }
  return path.startsWith("http") ? path : `${BACKEND_ORIGIN}${path}`;
}

const VIDEO_EXTENSIONS = [".mp4", ".webm", ".mov"];

/** Whether an uploaded media URL points at a video (vs. an image). */
export function isVideoUrl(url: string): boolean {
  const path = url.split("?", 1)[0].toLowerCase();
  return VIDEO_EXTENSIONS.some((ext) => path.endsWith(ext));
}

/** The bold identity line: a user's chosen display name, or their handle. */
export function displayName(user: {
  username: string;
  display_name?: string | null;
}): string {
  return user.display_name?.trim() || user.username;
}

type ApiErrorPayload = {
  detail?: string | Array<{ msg?: string; [key: string]: unknown }>;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = init.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = (await response.json()) as ApiErrorPayload;
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => (typeof item?.msg === "string" ? item.msg : JSON.stringify(item)))
          .join("; ");
      }
    } catch {
      message = response.statusText;
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function register(username: string, password: string): Promise<UserSummary> {
  return request<UserSummary>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function login(username: string, password: string): Promise<UserSummary> {
  return request<UserSummary>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

export function getCurrentUser(): Promise<UserSummary> {
  return request<UserSummary>("/auth/me");
}

export function uploadMedia(file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  return request<{ url: string }>("/media", {
    method: "POST",
    body: form,
  });
}

export function createTweet(content: string, mediaUrls: string[] = []): Promise<Tweet> {
  return request<Tweet>("/tweets", {
    method: "POST",
    body: JSON.stringify({ content, media_urls: mediaUrls }),
  });
}

export function editTweet(
  tweetId: number,
  content: string,
  mediaUrls: string[] = [],
): Promise<Tweet> {
  return request<Tweet>(`/tweets/${tweetId}`, {
    method: "PATCH",
    body: JSON.stringify({ content, media_urls: mediaUrls }),
  });
}

export function deleteTweet(tweetId: number): Promise<void> {
  return request<void>(`/tweets/${tweetId}`, { method: "DELETE" });
}

export function toggleTweetLike(tweetId: number): Promise<LikeToggleResult> {
  return request<LikeToggleResult>(`/tweets/${tweetId}/likes/toggle`, {
    method: "POST",
  });
}

export function getTweetStats(tweetIds: number[]): Promise<TweetStats[]> {
  const params = new URLSearchParams({ ids: tweetIds.join(",") });
  return request<TweetStats[]>(`/tweets/stats?${params.toString()}`);
}

export function createComment(
  tweetId: number,
  content: string,
  mediaUrls: string[] = [],
): Promise<Comment> {
  return request<Comment>(`/tweets/${tweetId}/comments`, {
    method: "POST",
    body: JSON.stringify({ content, media_urls: mediaUrls }),
  });
}

export function listComments(tweetId: number, limit = 100): Promise<Comment[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<Comment[]>(`/tweets/${tweetId}/comments?${params.toString()}`);
}

export function editComment(
  commentId: number,
  content: string,
  mediaUrls: string[] = [],
): Promise<Comment> {
  return request<Comment>(`/comments/${commentId}`, {
    method: "PATCH",
    body: JSON.stringify({ content, media_urls: mediaUrls }),
  });
}

export function deleteComment(commentId: number): Promise<void> {
  return request<void>(`/comments/${commentId}`, { method: "DELETE" });
}

export function toggleCommentLike(commentId: number): Promise<CommentLikeToggleResult> {
  return request<CommentLikeToggleResult>(`/comments/${commentId}/likes/toggle`, {
    method: "POST",
  });
}

export function getCommentStats(commentIds: number[]): Promise<CommentStats[]> {
  const params = new URLSearchParams({ ids: commentIds.join(",") });
  return request<CommentStats[]>(`/comments/stats?${params.toString()}`);
}

export function replyToComment(
  commentId: number,
  content: string,
  mediaUrls: string[] = [],
): Promise<Comment> {
  return request<Comment>(`/comments/${commentId}/comments`, {
    method: "POST",
    body: JSON.stringify({ content, media_urls: mediaUrls }),
  });
}

export function retweetComment(commentId: number): Promise<void> {
  return request<void>(`/comments/${commentId}/retweets`, { method: "POST" });
}

export function retweetTweet(tweetId: number): Promise<RetweetResult> {
  return request<RetweetResult>(`/tweets/${tweetId}/retweets`, { method: "POST" });
}

export function getTimeline(kind: TimelineKind, cursor?: string | null): Promise<TimelinePage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  const path =
    kind === "for-you"
      ? `/timeline/for-you?${params.toString()}`
      : `/timeline/home?${params.toString()}`;

  return request<TimelinePage>(path);
}

export function listUsers(query: string): Promise<UserDiscovery[]> {
  const params = new URLSearchParams({ limit: "10" });
  if (query.trim()) {
    params.set("query", query.trim());
  }
  return request<UserDiscovery[]>(`/users?${params.toString()}`);
}

export function followUser(userId: number): Promise<void> {
  return request<void>(`/follows/${userId}`, { method: "POST" });
}

export function unfollowUser(userId: number): Promise<void> {
  return request<void>(`/follows/${userId}`, { method: "DELETE" });
}

export function getTweet(tweetId: number): Promise<Tweet> {
  return request<Tweet>(`/tweets/${tweetId}`);
}

export function getUserProfile(username: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(username)}/profile`);
}

export function getUserTweets(
  username: string,
  cursor?: string | null,
): Promise<ProfileTweetsPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<ProfileTweetsPage>(
    `/users/${encodeURIComponent(username)}/tweets?${params.toString()}`,
  );
}

export function getUserReplies(
  username: string,
  cursor?: string | null,
): Promise<ProfileRepliesPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<ProfileRepliesPage>(
    `/users/${encodeURIComponent(username)}/replies?${params.toString()}`,
  );
}

export function updateProfile(
  fields: { display_name?: string; bio?: string },
): Promise<UserProfile> {
  return request<UserProfile>("/users/me", {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function listNotifications(): Promise<Notification[]> {
  return request<Notification[]>("/notifications");
}

export function getUnreadNotificationCount(): Promise<{ count: number }> {
  return request<{ count: number }>("/notifications/unread-count");
}

export function markNotificationsRead(): Promise<{ updated: number }> {
  return request<{ updated: number }>("/notifications/mark-read", { method: "POST" });
}

export function uploadAvatar(file: File): Promise<UserProfile> {
  const form = new FormData();
  form.append("file", file);
  return request<UserProfile>("/users/me/avatar", {
    method: "POST",
    body: form,
  });
}
