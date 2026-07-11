import type {
  BlockListPage,
  Comment,
  CommentLikeToggleResult,
  CommentStats,
  FollowListPage,
  LikeToggleResult,
  LinkPreview,
  MuteListPage,
  Notification,
  NotificationPage,
  Report,
  ReportReason,
  ProfileRepliesPage,
  ProfileTweetsPage,
  Session,
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
  is_deleted?: boolean;
}): string {
  if (user.is_deleted) {
    return "Deleted account";
  }
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

export function register(
  username: string,
  email: string,
  password: string,
): Promise<UserSummary> {
  return request<UserSummary>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, email, password }),
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

/**
 * Permanently delete the signed-in account (soft delete + PII scrub). Requires
 * the current password. The server revokes every session and clears the cookie,
 * so the caller is logged out; the app should drop its local user state.
 */
export function deleteAccount(password: string): Promise<void> {
  return request<void>("/auth/account", {
    method: "DELETE",
    body: JSON.stringify({ password }),
  });
}

/**
 * Rotate the password. Signs every *other* device out; the session cookie this
 * call gets back keeps the current one signed in.
 */
export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  return request<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

/** Claim a new address. The confirmed one does not move until the link is clicked. */
export function changeEmail(
  currentPassword: string,
  email: string,
): Promise<{ pending_email: string }> {
  return request<{ pending_email: string }>("/auth/change-email", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, email }),
  });
}

export function resendVerification(): Promise<{ pending_email: string }> {
  return request<{ pending_email: string }>("/auth/resend-verification", {
    method: "POST",
  });
}

export function verifyEmail(token: string): Promise<void> {
  return request<void>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

/**
 * Always resolves, for any address. The server answers 202 whether or not an
 * account exists, so the UI must not imply it learned anything either.
 */
export function forgotPassword(email: string): Promise<void> {
  return request<void>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, newPassword: string): Promise<void> {
  return request<void>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

export function getCurrentUser(): Promise<UserSummary> {
  return request<UserSummary>("/auth/me");
}

/** The caller's active sessions, most recently seen first. */
export function listSessions(): Promise<Session[]> {
  return request<Session[]>("/auth/sessions");
}

/** End every session except this one. Returns how many were revoked. */
export function logoutOtherSessions(): Promise<{ revoked: number }> {
  return request<{ revoked: number }>("/auth/logout-others", { method: "POST" });
}

/** End one other session by its opaque handle. */
export function revokeSession(id: string): Promise<void> {
  return request<void>(`/auth/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function uploadMedia(file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  return request<{ url: string }>("/media", {
    method: "POST",
    body: form,
  });
}

export function createTweet(
  content: string,
  mediaUrls: string[] = [],
  quotedPostId?: number,
): Promise<Tweet> {
  return request<Tweet>("/tweets", {
    method: "POST",
    body: JSON.stringify({
      content,
      media_urls: mediaUrls,
      ...(quotedPostId != null ? { quoted_post_id: quotedPostId } : {}),
    }),
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

export function getFollowers(
  username: string,
  cursor?: string | null,
): Promise<FollowListPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<FollowListPage>(
    `/users/${encodeURIComponent(username)}/followers?${params.toString()}`,
  );
}

export function getFollowing(
  username: string,
  cursor?: string | null,
): Promise<FollowListPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<FollowListPage>(
    `/users/${encodeURIComponent(username)}/following?${params.toString()}`,
  );
}

export function followUser(userId: number): Promise<void> {
  return request<void>(`/follows/${userId}`, { method: "POST" });
}

/** Block a user. Severs any follow between you and hides you from each other. */
export function blockUser(userId: number): Promise<{ is_blocked: boolean }> {
  return request<{ is_blocked: boolean }>(`/blocks/${userId}`, { method: "POST" });
}

export function unblockUser(userId: number): Promise<{ is_blocked: boolean }> {
  return request<{ is_blocked: boolean }>(`/blocks/${userId}`, { method: "DELETE" });
}

/** The accounts you have blocked, most recent first. */
export function listBlocked(cursor?: string | null): Promise<BlockListPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<BlockListPage>(`/blocks?${params.toString()}`);
}

/** Mute a user. Hides their content from you only; follows and replies stay. */
export function muteUser(userId: number): Promise<{ is_muted: boolean }> {
  return request<{ is_muted: boolean }>(`/mutes/${userId}`, { method: "POST" });
}

export function unmuteUser(userId: number): Promise<{ is_muted: boolean }> {
  return request<{ is_muted: boolean }>(`/mutes/${userId}`, { method: "DELETE" });
}

/** Report a post (tweet or comment) for a moderator to review. */
export function reportPost(
  postId: number,
  reason: ReportReason,
  details?: string,
): Promise<Report> {
  return request<Report>(`/reports/posts/${postId}`, {
    method: "POST",
    body: JSON.stringify({ reason, details: details?.trim() || null }),
  });
}

/** The accounts you have muted, most recent first. */
export function listMuted(cursor?: string | null): Promise<MuteListPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<MuteListPage>(`/mutes?${params.toString()}`);
}

export function unfollowUser(userId: number): Promise<void> {
  return request<void>(`/follows/${userId}`, { method: "DELETE" });
}

export function getTweet(tweetId: number): Promise<Tweet> {
  return request<Tweet>(`/tweets/${tweetId}`);
}

/**
 * Unfurl a URL into a preview card. Returns null when there is no usable
 * preview (unreachable, blocked, or no metadata) so callers just show the link.
 */
export async function unfurlUrl(url: string): Promise<LinkPreview | null> {
  try {
    return await request<LinkPreview>(
      `/link-preview?url=${encodeURIComponent(url)}`,
    );
  } catch {
    return null;
  }
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

export function listNotifications(cursor?: string | null): Promise<NotificationPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<NotificationPage>(`/notifications?${params.toString()}`);
}

export function getUnreadNotificationCount(): Promise<{ count: number }> {
  return request<{ count: number }>("/notifications/unread-count");
}

/** Mark a single notification read. */
export function markNotificationRead(id: number): Promise<void> {
  return request<void>(`/notifications/${id}/read`, { method: "POST" });
}

/** Mark every notification read. */
export function markNotificationsRead(): Promise<{ updated: number }> {
  return request<{ updated: number }>("/notifications/mark-read", { method: "POST" });
}

/** Absolute URL of the SSE notification stream, for an EventSource. */
export function notificationStreamUrl(): string {
  return `${API_BASE_URL}/notifications/stream`;
}

export function uploadAvatar(file: File): Promise<UserProfile> {
  const form = new FormData();
  form.append("file", file);
  return request<UserProfile>("/users/me/avatar", {
    method: "POST",
    body: form,
  });
}
