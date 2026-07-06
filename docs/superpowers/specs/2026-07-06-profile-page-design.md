# Profile Page — Design

**Date:** 2026-07-06
**Status:** Approved

## Goal

Add Twitter-style user profiles to the twitter_system app: view any user's profile
(own or others') with **Tweets** and **Replies** tabs, and edit your own profile
(avatar picture via file upload, bio/description). UI is mocked on Twitter.

## Decisions made during brainstorming

- **Avatar:** real file upload (multipart), stored on the backend's local disk and
  served statically. Not URL-paste, not preset avatars.
- **Replies tab:** each reply is rendered beneath the tweet it replied to (parent
  tweet card + thread line + reply), like real Twitter.
- **Profile header:** follower/following counts, Follow/Unfollow button, join date.
  No banner image upload — a plain accent-colored strip instead.
- **Navigation:** introduce `react-router-dom` with real URLs (user's explicit
  choice over keeping the existing state-based view switching).

## Routing architecture (frontend)

New dependency: `react-router-dom`.

| URL | View |
|---|---|
| `/` | Home timeline (For you / Following tabs, unchanged behavior) |
| `/tweet/:tweetId` | Tweet detail (replaces the current `selectedTweetId` state) |
| `/profile/:username` | Profile — Tweets tab |
| `/profile/:username/replies` | Profile — Replies tab |

- `MainApp` becomes a layout route: left nav and right sidebar persist, the center
  column renders the matched child route via `<Outlet>`.
- Unauthenticated users see the auth screen for any URL; after login they land on
  the originally requested URL. No separate `/login` route.
- Profiles are addressed by **username** in the URL. The profile API response
  includes the numeric `id` so the existing id-based follow/unfollow API works.
- Tweet detail keeps its component largely as-is; it reads `tweetId` from
  `useParams` and "back" uses browser history.

## Backend — data model

`User` gains two nullable columns (dev schema sync auto-adds nullable columns to
SQLite; no manual migration):

- `bio: String(160)` — Twitter's bio length limit
- `avatar_url: String(255)` — server-relative path, e.g. `/uploads/avatars/3.webp`

`UserSummary` (the `author` object embedded in every tweet and comment response)
gains `avatar_url`, so avatars render on tweet/comment cards app-wide.

## Backend — API

All new routes live in `backend/app/api/routes/users.py`. String `{username}`
path params do not collide with existing `/users` routes (which have no path
suffixes).

| Endpoint | Behavior |
|---|---|
| `GET /users/{username}/profile` | Returns id, username, bio, avatar_url, created_at, follower_count, following_count, tweet_count, is_following, is_current_user. 404 if the username doesn't exist. |
| `GET /users/{username}/tweets?cursor&limit` | The user's tweets newest-first, same item shape as timeline items (counts + `liked_by_me`), cursor-paginated. Reuses `list_tweets_by_authors` with a single author. |
| `GET /users/{username}/replies?cursor&limit` | The user's comments newest-first; each item embeds the full parent tweet (tweet-card shape). Replies whose parent tweet was deleted are skipped. |
| `PATCH /users/me` | JSON body `{bio}`; updates the current user's bio. |
| `POST /users/me/avatar` | Multipart image upload. Accepts JPEG/PNG/WebP, max 2 MB. Saved to `backend/uploads/avatars/{user_id}.{ext}`, overwriting any previous avatar. Updates `avatar_url` and returns the updated profile. Invalid type or oversize returns 4xx with a descriptive detail message. |

Static serving: `main.py` mounts `app.mount("/uploads", StaticFiles(directory="uploads"))`.
The `uploads/` directory is created on startup if missing and gets a `.gitignore`.
The frontend prefixes the backend origin when rendering `avatar_url`.

## Frontend — UI

**ProfileView** (new file `frontend/src/ProfileView.tsx`), Twitter-style:

- Sticky back-arrow header with the user's name and tweet count.
- Accent-colored banner strip; large round avatar overlapping its bottom edge.
- Display name, `@username`, bio, "Joined <Month Year>" from `created_at`,
  "**N** Following · **N** Followers".
- Header button: **Edit profile** on own profile; **Follow / Following** on
  others (Following flips to an unfollow affordance on hover).
- Tabs **Tweets** | **Replies** with underlined active tab; switching tabs
  changes the URL (`/replies` suffix).
- Tweets tab reuses the existing tweet card component (like/comment/retweet all
  functional). Replies tab renders parent tweet card + thread line + the user's
  reply with "Replying to @author".
- "Load more" cursor pagination, same pattern as the timeline.

**EditProfileModal**: avatar preview with camera-icon overlay to pick a file
(client-side preview before upload), bio textarea with a 160-character counter,
Save/Cancel. Save uploads the avatar (if changed) then patches the bio. Errors
show inline in the modal.

**Avatar component**: one shared `Avatar` component — image when `avatar_url` is
set, otherwise the existing initial-letter style. Used in tweet cards, comments,
nav, and profile. Clicking an avatar or username anywhere navigates to
`/profile/:username`.

## Error handling

- Unknown username → "This account doesn't exist" empty state on the profile page.
- Avatar upload rejections (wrong type, too large) → API detail message shown in
  the edit modal.
- Feed/tab load failures reuse the existing inline error + retry pattern.

## Testing

- Backend: pytest tests in `backend/tests/` for the five new endpoints —
  profile counts and `is_following`, tweets tab pagination, replies-with-parent
  shape (including deleted-parent skip), bio patch validation, avatar upload
  type/size validation.
- Frontend: verified end-to-end by driving the app (two users: edit one profile,
  view/follow from the other).

## Out of scope

- Banner image upload
- Display names separate from usernames
- Media/Likes profile tabs
- Follower/following list pages
- Image resizing/cropping on upload
