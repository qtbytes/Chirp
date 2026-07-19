import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Loader2, ShieldCheck } from "lucide-react";

import { useFeedMemory } from "./useFeedMemory";

import {
  displayName,
  getModerationQueue,
  moderationDismiss,
  moderationDismissUserReports,
  moderationRestore,
  moderationSuspendUser,
  moderationTakedown,
  moderationUnsuspendUser,
} from "./api";
import { Avatar, formatCompactDate, getErrorMessage } from "./components";
import type { ModerationQueueItem } from "./types";

type QueueTab = "open" | "resolved";

/** Stable identity for an item across both target shapes. */
function targetKey(item: ModerationQueueItem): string {
  return item.post ? `post-${item.post.id}` : `user-${item.reported_user!.id}`;
}

/**
 * The moderation queue: reported posts and reported accounts, one card per
 * target, judged with one action each. Reachable only through the rail link
 * the server-side `is_moderator` flag reveals; the API 404s anyone else.
 *
 * The tab lives in the URL (like profile tabs) so back/forward lands on the
 * queue the moderator left, and `useFeedMemory` re-hydrates its loaded pages
 * — without it, ScrollMemory's restored offset would point into a list that
 * a fresh first-page fetch no longer contains.
 */
export default function ModerationView() {
  const location = useLocation();
  const tab: QueueTab = location.pathname.endsWith("/resolved")
    ? "resolved"
    : "open";
  const [items, setItems] = useState<ModerationQueueItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actingOn, setActingOn] = useState<string | null>(null);

  const takeFeedMemory = useFeedMemory(
    `moderation:${tab}`,
    { items, cursor },
    items.length === 0,
  );

  const load = useCallback(
    async (activeTab: QueueTab, nextCursor?: string | null, append = false) => {
      setLoading(true);
      setError("");
      try {
        const page = await getModerationQueue(activeTab, nextCursor);
        setItems((current) => (append ? [...current, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    setItems([]);
    setCursor(null);
    // Back/forward restores the queue as the moderator left it.
    const cached = takeFeedMemory();
    if (cached) {
      setItems(cached.items);
      setCursor(cached.cursor);
      setLoading(false);
      return;
    }
    void load(tab);
  }, [tab, load, takeFeedMemory]);

  async function act(
    item: ModerationQueueItem,
    action: "dismiss" | "takedown" | "restore",
  ) {
    const key = targetKey(item);
    setActingOn(key);
    setError("");
    try {
      if (action === "dismiss") {
        await moderationDismiss(item.post!.id);
      } else if (action === "takedown") {
        await moderationTakedown(item.post!.id);
      } else {
        await moderationRestore(item.post!.id);
      }
      if (action === "restore") {
        // The judgement stays on the resolved list; only the effect flips.
        setItems((current) =>
          current.map((row) =>
            targetKey(row) === key
              ? { ...row, post: { ...row.post!, taken_down: false } }
              : row,
          ),
        );
      } else {
        // Judged: the post leaves the open queue.
        setItems((current) => current.filter((row) => targetKey(row) !== key));
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActingOn(null);
    }
  }

  /** Flip an account's suspended flag everywhere it appears in the queue. */
  function applySuspension(userId: number, suspended: boolean) {
    setItems((current) =>
      current.map((row) => {
        if (row.post && row.post.author.id === userId) {
          return {
            ...row,
            post: { ...row.post, author: { ...row.post.author, is_suspended: suspended } },
          };
        }
        if (row.reported_user && row.reported_user.id === userId) {
          return {
            ...row,
            reported_user: { ...row.reported_user, is_suspended: suspended },
          };
        }
        return row;
      }),
    );
  }

  /** Judge a reported *account*: dismiss its reports or (un)suspend it. */
  async function actOnUser(
    item: ModerationQueueItem,
    action: "dismiss" | "suspend" | "unsuspend",
  ) {
    const user = item.reported_user!;
    const key = targetKey(item);
    setActingOn(key);
    setError("");
    try {
      if (action === "dismiss") {
        await moderationDismissUserReports(user.id);
      } else if (action === "suspend") {
        await moderationSuspendUser(user.id);
      } else {
        await moderationUnsuspendUser(user.id);
      }
      if (action === "unsuspend") {
        // Like restore: the judgement stays resolved, only the effect flips.
        applySuspension(user.id, false);
      } else if (tab === "open") {
        // Dismissed or suspended: either way the reports resolved, so the
        // account leaves the open queue.
        setItems((current) => current.filter((row) => targetKey(row) !== key));
        if (action === "suspend") {
          applySuspension(user.id, true);
        }
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActingOn(null);
    }
  }

  async function toggleSuspension(item: ModerationQueueItem) {
    const author = item.post!.author;
    setActingOn(targetKey(item));
    setError("");
    try {
      const result = author.is_suspended
        ? await moderationUnsuspendUser(author.id)
        : await moderationSuspendUser(author.id);
      // The author may appear on several queue items; keep them all honest.
      applySuspension(author.id, result.suspended);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActingOn(null);
    }
  }

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Moderation</h1>
        </div>
        <nav className="tab-list" role="tablist" aria-label="Report queues">
          <Link
            to="/moderation"
            className={tab === "open" ? "tab active" : "tab"}
            role="tab"
            aria-selected={tab === "open"}
          >
            Open
          </Link>
          <Link
            to="/moderation/resolved"
            className={tab === "resolved" ? "tab active" : "tab"}
            role="tab"
            aria-selected={tab === "resolved"}
          >
            Resolved
          </Link>
        </nav>
      </header>

      {error ? <div className="status-panel error">{error}</div> : null}

      {!loading && items.length === 0 && !error ? (
        <div className="empty-state">
          <ShieldCheck
            size={72}
            strokeWidth={1.4}
            className="empty-state-icon empty-state-icon--muted"
            aria-hidden="true"
          />
          <p>{tab === "open" ? "No open reports. All clear!" : "Nothing resolved yet."}</p>
        </div>
      ) : null}

      <ul className="mod-queue">
        {items.map((item) => {
          const key = targetKey(item);
          const acting = actingOn === key;
          const post = item.post;
          const reportedUser = item.reported_user;
          return (
            <li key={key} className="mod-item">
              {post ? (
                <div className="mod-post">
                  <div className="mod-post-author">
                    <Avatar user={post.author} size="small" />
                    <Link
                      to={`/${encodeURIComponent(post.author.username)}`}
                      className="author-link"
                    >
                      <strong>{displayName(post.author)}</strong>
                      <span className="mod-username">@{post.author.username}</span>
                    </Link>
                    <span className="mod-time">{formatCompactDate(post.created_at)}</span>
                    {post.taken_down ? (
                      <span className="mod-flag">taken down</span>
                    ) : null}
                    {post.author.is_suspended ? (
                      <span className="mod-flag">author suspended</span>
                    ) : null}
                  </div>
                  <p className="mod-content">{post.content || "(no text)"}</p>
                  {post.media_urls.length > 0 ? (
                    <p className="mod-media-note">
                      {post.media_urls.length} media attachment
                      {post.media_urls.length > 1 ? "s" : ""}
                    </p>
                  ) : null}
                  <Link className="mod-thread-link" to={`/tweet/${post.thread_id}`}>
                    View {post.is_reply ? "thread" : "post"}
                  </Link>
                </div>
              ) : (
                <div className="mod-post">
                  <div className="mod-post-author">
                    <Avatar user={reportedUser!} size="small" />
                    <Link
                      to={`/${encodeURIComponent(reportedUser!.username)}`}
                      className="author-link"
                    >
                      <strong>{displayName(reportedUser!)}</strong>
                      <span className="mod-username">@{reportedUser!.username}</span>
                    </Link>
                    <span className="mod-flag mod-flag--kind">account</span>
                    {reportedUser!.is_suspended ? (
                      <span className="mod-flag">suspended</span>
                    ) : null}
                    {reportedUser!.is_deleted ? (
                      <span className="mod-flag">deleted</span>
                    ) : null}
                  </div>
                  <p className="mod-content mod-content--account">
                    The account itself was reported — its profile and messages
                    are the evidence.
                  </p>
                  <Link
                    className="mod-thread-link"
                    to={`/${encodeURIComponent(reportedUser!.username)}`}
                  >
                    View profile
                  </Link>
                </div>
              )}

              <div className="mod-reports">
                <span className="mod-count">
                  {item.report_count} report{item.report_count > 1 ? "s" : ""}
                </span>
                <ul>
                  {item.reports.map((report) => (
                    <li key={report.id}>
                      <strong>@{report.reporter.username}</strong>{" "}
                      <span className="mod-reason">{report.reason}</span>
                      {report.details ? <> — {report.details}</> : null}
                    </li>
                  ))}
                </ul>
              </div>

              {post ? (
                <div className="mod-actions">
                  <button
                    className="mod-pill-button"
                    disabled={acting}
                    onClick={() => void toggleSuspension(item)}
                  >
                    {post.author.is_suspended ? "Unsuspend author" : "Suspend author"}
                  </button>
                  {tab === "open" ? (
                    <>
                      <button
                        className="mod-pill-button"
                        disabled={acting}
                        onClick={() => void act(item, "dismiss")}
                      >
                        Dismiss
                      </button>
                      <button
                        className="danger-button"
                        disabled={acting}
                        onClick={() => void act(item, "takedown")}
                      >
                        Take down
                      </button>
                    </>
                  ) : post.taken_down ? (
                    <button
                      className="mod-pill-button"
                      disabled={acting}
                      onClick={() => void act(item, "restore")}
                    >
                      Restore
                    </button>
                  ) : (
                    <span className="mod-resolved-note">
                      {item.reports.some((report) => report.status === "actioned")
                        ? "restored"
                        : "dismissed"}
                    </span>
                  )}
                </div>
              ) : (
                <div className="mod-actions">
                  {tab === "open" ? (
                    <>
                      <button
                        className="mod-pill-button"
                        disabled={acting}
                        onClick={() => void actOnUser(item, "dismiss")}
                      >
                        Dismiss
                      </button>
                      {reportedUser!.is_suspended ? (
                        <button
                          className="mod-pill-button"
                          disabled={acting}
                          onClick={() => void actOnUser(item, "unsuspend")}
                        >
                          Unsuspend
                        </button>
                      ) : (
                        <button
                          className="danger-button"
                          disabled={acting || reportedUser!.is_deleted}
                          onClick={() => void actOnUser(item, "suspend")}
                        >
                          Suspend
                        </button>
                      )}
                    </>
                  ) : reportedUser!.is_suspended ? (
                    <button
                      className="mod-pill-button"
                      disabled={acting}
                      onClick={() => void actOnUser(item, "unsuspend")}
                    >
                      Unsuspend
                    </button>
                  ) : (
                    <span className="mod-resolved-note">
                      {item.reports.some((report) => report.status === "actioned")
                        ? "unsuspended"
                        : "dismissed"}
                    </span>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {cursor ? (
        <button
          className="load-more"
          onClick={() => void load(tab, cursor, true)}
          disabled={loading}
        >
          Load more
        </button>
      ) : null}
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading reports</span>
        </div>
      ) : null}
    </>
  );
}
