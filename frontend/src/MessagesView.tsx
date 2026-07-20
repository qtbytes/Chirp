import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUp,
  Ban,
  Bell,
  BellOff,
  Check,
  Loader2,
  MailPlus,
  MessageCirclePlus,
  MoreHorizontal,
  Search as SearchIcon,
  Settings2,
  Trash2,
  User as UserIcon,
  X,
} from "lucide-react";
import {
  ApiError,
  blockUser,
  deleteDmChat,
  displayName,
  getDmChat,
  getDmConversations,
  getUserProfile,
  listUsers,
  markDmRead,
  sendDmMessage,
  setDmChatMuted,
  unblockUser,
  updateProfile,
} from "./api";
import type {
  DmChat,
  DmConversation,
  DmMessage,
  DmPolicy,
  UserDiscovery,
  UserSummary,
} from "./types";
import {
  Avatar,
  ConfirmDialog,
  formatCompactDate,
  getErrorMessage,
  parseBackendDate,
} from "./components";
import { EmojiPicker } from "./EmojiPicker";
import { useEmojiField } from "./useEmojiField";
import { InfiniteScroll } from "./InfiniteScroll";

const POLICY_LABELS: Record<DmPolicy, string> = {
  everyone: "Everyone",
  following: "People I follow",
  none: "No one",
};

/** The /messages inbox: conversation list, DM settings, and new chat. */
export function MessagesView({ currentUser }: { currentUser: UserSummary }) {
  const navigate = useNavigate();
  const [conversations, setConversations] = useState<DmConversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [composing, setComposing] = useState(false);

  const load = useCallback(async (nextCursor?: string | null, append = false) => {
    setLoading(true);
    setError("");
    try {
      const page = await getDmConversations(nextCursor);
      setConversations((current) =>
        append ? [...current, ...page.items] : page.items,
      );
      setCursor(page.next_cursor);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="messages-view" aria-label="Direct messages">
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Chats</h1>
          <div className="chat-header-actions">
            <DmSettingsMenu username={currentUser.username} />
            <button
              className="primary-button compact"
              onClick={() => setComposing(true)}
            >
              <MailPlus size={16} aria-hidden="true" />
              <span>New chat</span>
            </button>
          </div>
        </div>
      </header>

      {error ? <div className="status-panel error">{error}</div> : null}
      {!loading && conversations.length === 0 && !error ? (
        <div className="empty-state">
          {/* A smiling speech bubble (lucide's message-circle with a face). */}
          <svg
            className="empty-state-icon"
            width="72"
            height="72"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
            <circle cx="9.2" cy="10.2" r="0.5" fill="currentColor" />
            <circle cx="14.8" cy="10.2" r="0.5" fill="currentColor" />
            <path d="M9.6 13.4c1.4 1.2 3.4 1.2 4.8 0" />
          </svg>
          <p>Say hi to someone</p>
          <button
            className="primary-button empty-state-button"
            onClick={() => setComposing(true)}
          >
            <MessageCirclePlus size={18} aria-hidden="true" />
            <span>New chat</span>
          </button>
        </div>
      ) : null}

      <div className="chat-list">
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className="chat-row"
            role="link"
            tabIndex={0}
            onClick={() =>
              navigate(`/messages/${encodeURIComponent(conversation.other_user.username)}`)
            }
            onKeyDown={(event) => {
              if (event.key === "Enter" && event.target === event.currentTarget) {
                navigate(
                  `/messages/${encodeURIComponent(conversation.other_user.username)}`,
                );
              }
            }}
          >
            <Avatar user={conversation.other_user} />
            <span className="chat-row-body">
              <span className="chat-row-top">
                <strong>{displayName(conversation.other_user)}</strong>
                <span className="chat-row-handle">
                  @{conversation.other_user.username}
                </span>
                {conversation.last_message ? (
                  <span className="chat-row-time">
                    · {formatCompactDate(conversation.last_message.created_at)}
                  </span>
                ) : null}
                {conversation.muted ? (
                  <BellOff
                    size={14}
                    className="chat-muted-icon"
                    aria-label="Muted conversation"
                  />
                ) : null}
              </span>
              <span
                className={
                  conversation.unread_count > 0
                    ? "chat-row-preview unread"
                    : "chat-row-preview"
                }
              >
                {conversation.last_message
                  ? (conversation.last_message.sender_id === currentUser.id
                      ? "You: "
                      : "") + conversation.last_message.content
                  : ""}
              </span>
            </span>
            {conversation.unread_count > 0 ? (
              <span className="chat-unread-badge">
                {conversation.unread_count > 99 ? "99+" : conversation.unread_count}
              </span>
            ) : null}
            <span
              className="chat-row-menu"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              <ChatMenu
                otherUser={conversation.other_user}
                muted={conversation.muted}
                blocked={conversation.blocked}
                onMutedChange={(muted) =>
                  setConversations((current) =>
                    current.map((item) =>
                      item.id === conversation.id ? { ...item, muted } : item,
                    ),
                  )
                }
                onBlockChanged={() => void load()}
                onDeleted={() =>
                  setConversations((current) =>
                    current.filter((item) => item.id !== conversation.id),
                  )
                }
              />
            </span>
          </div>
        ))}
      </div>

      <InfiniteScroll
        hasMore={!!cursor}
        loading={loading}
        onLoadMore={() => void load(cursor, true)}
      />
      {loading ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : null}

      {composing ? <NewChatDialog onClose={() => setComposing(false)} /> : null}
    </section>
  );
}

/**
 * The per-conversation "…" menu (inbox row and chat header): go to profile,
 * mute/unmute, block the account, or delete the conversation for yourself.
 */
function ChatMenu({
  otherUser,
  muted,
  blocked,
  onMutedChange,
  onBlockChanged,
  onDeleted,
}: {
  otherUser: UserSummary;
  muted: boolean;
  /** Whether the viewer has blocked them; flips the item to Unblock. */
  blocked: boolean;
  onMutedChange: (muted: boolean) => void;
  /** A block was added or removed; the host should re-read its chat state. */
  onBlockChanged: () => void;
  /** The conversation was deleted for this side; it left the inbox. */
  onDeleted: () => void;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<"block" | "delete" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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

  async function toggleMute() {
    setOpen(false);
    try {
      await setDmChatMuted(otherUser.username, !muted);
      onMutedChange(!muted);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function confirmAction() {
    if (!confirming) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (confirming === "block") {
        await blockUser(otherUser.id);
        setConfirming(null);
        onBlockChanged();
      } else {
        await deleteDmChat(otherUser.username);
        setConfirming(null);
        onDeleted();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="profile-menu" ref={ref}>
      <button
        type="button"
        className="icon-button"
        onClick={() => setOpen((value) => !value)}
        aria-label={`Conversation options for @${otherUser.username}`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
      {open ? (
        <div className="post-menu-dropdown chat-menu-dropdown" role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              navigate(`/${encodeURIComponent(otherUser.username)}`);
            }}
          >
            <UserIcon size={16} aria-hidden="true" />
            <span>Go to profile</span>
          </button>
          <button type="button" role="menuitem" onClick={() => void toggleMute()}>
            {muted ? (
              <Bell size={16} aria-hidden="true" />
            ) : (
              <BellOff size={16} aria-hidden="true" />
            )}
            <span>{muted ? "Unmute conversation" : "Mute conversation"}</span>
          </button>
          {blocked ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                // Unblocking restores, so it needs no confirmation.
                void unblockUser(otherUser.id)
                  .then(onBlockChanged)
                  .catch((err) => setError(getErrorMessage(err)));
              }}
            >
              <Ban size={16} aria-hidden="true" />
              <span>Unblock account</span>
            </button>
          ) : (
            <button
              type="button"
              role="menuitem"
              className="danger-menu-item"
              onClick={() => {
                setOpen(false);
                setConfirming("block");
              }}
            >
              <Ban size={16} aria-hidden="true" />
              <span>Block account</span>
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="danger-menu-item"
            onClick={() => {
              setOpen(false);
              setConfirming("delete");
            }}
          >
            <Trash2 size={16} aria-hidden="true" />
            <span>Delete conversation</span>
          </button>
          {error ? <p className="form-error">{error}</p> : null}
        </div>
      ) : null}
      {confirming === "block" ? (
        <ConfirmDialog
          title={`Block @${otherUser.username}?`}
          message="Neither of you can send messages anymore, though the chat history stays. They also can't follow you or see your Tweets."
          confirmLabel="Block"
          busyLabel="Blocking…"
          busy={busy}
          onConfirm={() => void confirmAction()}
          onCancel={() => setConfirming(null)}
        />
      ) : null}
      {confirming === "delete" ? (
        <ConfirmDialog
          title="Delete this conversation?"
          message={`The messages disappear for you, but @${otherUser.username} keeps their copy. If either of you writes again, the chat starts fresh from there.`}
          confirmLabel="Delete"
          busyLabel="Deleting…"
          busy={busy}
          onConfirm={() => void confirmAction()}
          onCancel={() => setConfirming(null)}
        />
      ) : null}
    </div>
  );
}

/** The gear menu: who may message you, persisted to the profile. */
function DmSettingsMenu({ username }: { username: string }) {
  const [open, setOpen] = useState(false);
  const [policy, setPolicy] = useState<DmPolicy | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Lazily read the current setting the first time the menu opens.
    if (!open || policy !== null) {
      return;
    }
    getUserProfile(username)
      .then((profile) => setPolicy(profile.dm_policy ?? "everyone"))
      .catch((err) => setError(getErrorMessage(err)));
  }, [open, policy, username]);

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

  async function choose(next: DmPolicy) {
    setSaving(true);
    setError("");
    try {
      await updateProfile({ dm_policy: next });
      setPolicy(next);
      setOpen(false);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="profile-menu" ref={ref}>
      <button
        type="button"
        className="outline-icon-button"
        onClick={() => setOpen((value) => !value)}
        aria-label="Message settings"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Settings2 size={18} aria-hidden="true" />
      </button>
      {open ? (
        <div className="post-menu-dropdown dm-settings-dropdown" role="menu">
          <p className="dm-settings-title">Allow new messages from</p>
          {(Object.keys(POLICY_LABELS) as DmPolicy[]).map((option) => (
            <button
              type="button"
              role="menuitemradio"
              aria-checked={policy === option}
              key={option}
              disabled={saving || policy === null}
              onClick={() => void choose(option)}
            >
              <span className="dm-settings-check">
                {policy === option ? <Check size={16} aria-hidden="true" /> : null}
              </span>
              <span>{POLICY_LABELS[option]}</span>
            </button>
          ))}
          <p className="dm-settings-note">
            Anyone can send one message; more only after you reply. Chats where
            you already replied stay open.
          </p>
          {error ? <p className="form-error">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * "New message" (Twitter-style): search people you can actually DM -- the
 * server filters out accounts whose policy or a block refuses you, rather
 * than listing them grayed out -- then jump into the conversation.
 */
function NewChatDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserDiscovery[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearching(true);
      listUsers(trimmed, { messageable: true })
        .then((users) => {
          if (!cancelled) {
            setResults(users.filter((user) => !user.is_current_user));
          }
        })
        .catch((err) => {
          if (!cancelled) setError(getErrorMessage(err));
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal new-chat-modal"
        role="dialog"
        aria-label="New message"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h2>New message</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="new-chat-search">
          <SearchIcon size={18} aria-hidden="true" />
          <input
            autoFocus
            placeholder="Search people"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="new-chat-results">
          {results.map((user) => (
            <button
              key={user.id}
              className="new-chat-user"
              onClick={() => navigate(`/messages/${encodeURIComponent(user.username)}`)}
            >
              <Avatar user={user} />
              <span className="new-chat-user-copy">
                <strong>{displayName(user)}</strong>
                <span>@{user.username}</span>
              </span>
            </button>
          ))}
          {searching ? (
            <div className="loading-row">
              <Loader2 className="spin" size={18} aria-hidden="true" />
              <span>Searching</span>
            </div>
          ) : null}
          {!searching && query.trim() && results.length === 0 && !error ? (
            <p className="new-chat-empty">No one found who can be messaged.</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The /messages/:username conversation view. */
export function ChatView({ currentUser }: { currentUser: UserSummary }) {
  const { username = "" } = useParams();
  const navigate = useNavigate();
  const [chat, setChat] = useState<DmChat | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [unblocking, setUnblocking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const { insertEmoji, fieldProps } = useEmojiField<HTMLTextAreaElement>(
    draft,
    setDraft,
    1000,
  );

  const refresh = useCallback(async () => {
    try {
      const loaded = await getDmChat(username);
      setChat((current) => {
        // Keep messages loaded via "older" paging that the fresh first page
        // no longer covers.
        if (!current) {
          return loaded;
        }
        const fresh = new Set(loaded.messages.map((message) => message.id));
        const keptOlder = current.messages.filter(
          (message) => !fresh.has(message.id),
        );
        return {
          ...loaded,
          messages: [...loaded.messages, ...keptOlder],
          next_cursor: current.next_cursor ?? loaded.next_cursor,
        };
      });
      setError("");
      // Whatever just arrived is on screen; tell the server it is read.
      void markDmRead(username).catch(() => undefined);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setNotFound(true);
      } else {
        setError(getErrorMessage(err));
      }
    }
  }, [username]);

  useEffect(() => {
    setChat(null);
    setNotFound(false);
    setError("");
    stickToBottom.current = true;
    void refresh();
    // A gentle poll keeps the open chat close to live; the SSE nudge only
    // updates the rail badge, so this is the chat's own freshness source.
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (stickToBottom.current) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [chat?.messages.length]);

  async function loadOlder() {
    if (!chat?.next_cursor || loadingOlder) {
      return;
    }
    setLoadingOlder(true);
    stickToBottom.current = false;
    try {
      const older = await getDmChat(username, Number(chat.next_cursor));
      setChat((current) =>
        current
          ? {
              ...current,
              messages: [...current.messages, ...older.messages],
              next_cursor: older.next_cursor,
            }
          : current,
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoadingOlder(false);
    }
  }

  async function unblock() {
    if (unblocking) {
      return;
    }
    setUnblocking(true);
    try {
      await unblockUser(chat!.other_user.id);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setUnblocking(false);
    }
  }

  async function send() {
    const content = draft.trim();
    if (!content || sending || !chat?.can_send) {
      return;
    }
    setSending(true);
    setSendError("");
    stickToBottom.current = true;
    try {
      await sendDmMessage(username, content);
      setDraft("");
      await refresh();
    } catch (err) {
      setSendError(getErrorMessage(err));
      void refresh();
    } finally {
      setSending(false);
    }
  }

  if (notFound) {
    return (
      <section className="chat-view">
        <div className="detail-toolbar">
          <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h2>Chat</h2>
        </div>
        <div className="status-panel">
          <strong>This account is unavailable</strong>
          <p>The account doesn&apos;t exist or can&apos;t be messaged.</p>
        </div>
      </section>
    );
  }

  if (!chat) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading chat</span>
      </div>
    );
  }

  // API order is newest-first; the transcript reads oldest-first.
  const transcript = [...chat.messages].reverse();

  return (
    <section className="chat-view" aria-label={`Chat with ${chat.other_user.username}`}>
      <div className="detail-toolbar">
        <button
          className="icon-button"
          onClick={() => navigate("/messages")}
          aria-label="Back to chats"
        >
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <Link
          to={`/${encodeURIComponent(chat.other_user.username)}`}
          className="author-link chat-peer"
        >
          <Avatar user={chat.other_user} size="small" />
          <div className="chat-peer-copy">
            <strong>
              {displayName(chat.other_user)}
              {chat.muted ? (
                <BellOff
                  size={14}
                  className="chat-muted-icon"
                  aria-label="Muted conversation"
                />
              ) : null}
            </strong>
            <span>@{chat.other_user.username}</span>
          </div>
        </Link>
        <span className="chat-toolbar-menu">
          <ChatMenu
            otherUser={chat.other_user}
            muted={chat.muted}
            blocked={chat.blocked}
            onMutedChange={(muted) =>
              setChat((current) => (current ? { ...current, muted } : current))
            }
            onBlockChanged={() => void refresh()}
            onDeleted={() => navigate("/messages")}
          />
        </span>
      </div>

      {error ? <div className="status-panel error">{error}</div> : null}

      <div className="chat-transcript">
        {chat.next_cursor ? (
          <button
            className="load-more"
            onClick={() => void loadOlder()}
            disabled={loadingOlder}
          >
            Load older messages
          </button>
        ) : null}
        {transcript.length === 0 ? (
          <div className="chat-empty">
            <p>
              Say hi to <strong>{displayName(chat.other_user)}</strong>!
            </p>
          </div>
        ) : null}
        {transcript.map((message: DmMessage) => (
          <div
            key={message.id}
            className={
              message.sender_id === currentUser.id
                ? "chat-bubble-row own"
                : "chat-bubble-row"
            }
          >
            <div className="chat-bubble">
              <p>{message.content}</p>
              <time dateTime={message.created_at}>
                {new Intl.DateTimeFormat(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                }).format(parseBackendDate(message.created_at))}
              </time>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="chat-composer-holder">
        {chat.can_send ? (
          <form
            className="chat-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <div className="chat-input-pill">
              <textarea
                {...fieldProps}
                rows={1}
                value={draft}
                maxLength={1000}
                placeholder={`Message @${chat.other_user.username}`}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              <EmojiPicker onSelect={insertEmoji} />
            </div>
            <button
              type="submit"
              className="chat-send-button"
              disabled={sending || !draft.trim()}
              aria-label="Send message"
            >
              <ArrowUp size={18} aria-hidden="true" />
            </button>
          </form>
        ) : (
          <div className="chat-locked" role="note">
            {chat.cannot_send_reason === "await_reply" ? (
              <>
                You can send more messages after @{chat.other_user.username}{" "}
                replies.
              </>
            ) : chat.cannot_send_reason === "you_blocked" ? (
              <>
                You blocked @{chat.other_user.username}.{" "}
                <button
                  className="text-button inline"
                  disabled={unblocking}
                  onClick={() => void unblock()}
                >
                  Unblock
                </button>{" "}
                them to send messages again.
              </>
            ) : chat.cannot_send_reason === "blocked_you" ? (
              <>
                @{chat.other_user.username} has blocked you. You can&apos;t send
                them messages.
              </>
            ) : (
              <>@{chat.other_user.username} doesn&apos;t accept new messages.</>
            )}
          </div>
        )}
        {sendError ? <p className="form-error">{sendError}</p> : null}
      </div>
    </section>
  );
}
