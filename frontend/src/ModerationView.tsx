import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, ShieldCheck } from "lucide-react";

import {
  displayName,
  getModerationQueue,
  moderationDismiss,
  moderationRestore,
  moderationSuspendUser,
  moderationTakedown,
  moderationUnsuspendUser,
} from "./api";
import { Avatar, formatCompactDate, getErrorMessage } from "./components";
import type { ModerationQueueItem } from "./types";

type QueueTab = "open" | "resolved";

/**
 * The moderation queue: reported posts, one card per post, judged with one
 * action each. Reachable only through the rail link the server-side
 * `is_moderator` flag reveals; the API 404s anyone else.
 */
export default function ModerationView() {
  const [tab, setTab] = useState<QueueTab>("open");
  const [items, setItems] = useState<ModerationQueueItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actingOn, setActingOn] = useState<number | null>(null);

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
    void load(tab);
  }, [tab, load]);

  async function act(
    item: ModerationQueueItem,
    action: "dismiss" | "takedown" | "restore",
  ) {
    setActingOn(item.post.id);
    setError("");
    try {
      if (action === "dismiss") {
        await moderationDismiss(item.post.id);
      } else if (action === "takedown") {
        await moderationTakedown(item.post.id);
      } else {
        await moderationRestore(item.post.id);
      }
      if (action === "restore") {
        // The judgement stays on the resolved list; only the effect flips.
        setItems((current) =>
          current.map((row) =>
            row.post.id === item.post.id
              ? { ...row, post: { ...row.post, taken_down: false } }
              : row,
          ),
        );
      } else {
        // Judged: the post leaves the open queue.
        setItems((current) =>
          current.filter((row) => row.post.id !== item.post.id),
        );
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActingOn(null);
    }
  }

  async function toggleSuspension(item: ModerationQueueItem) {
    const author = item.post.author;
    setActingOn(item.post.id);
    setError("");
    try {
      const result = author.is_suspended
        ? await moderationUnsuspendUser(author.id)
        : await moderationSuspendUser(author.id);
      // The author may appear on several queue items; keep them all honest.
      setItems((current) =>
        current.map((row) =>
          row.post.author.id === author.id
            ? {
                ...row,
                post: {
                  ...row.post,
                  author: { ...row.post.author, is_suspended: result.suspended },
                },
              }
            : row,
        ),
      );
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
        <nav className="tab-list" aria-label="Report queues">
          <button
            className={tab === "open" ? "tab active" : "tab"}
            onClick={() => setTab("open")}
          >
            Open
          </button>
          <button
            className={tab === "resolved" ? "tab active" : "tab"}
            onClick={() => setTab("resolved")}
          >
            Resolved
          </button>
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
          const post = item.post;
          const acting = actingOn === post.id;
          return (
            <li key={post.id} className="mod-item">
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
