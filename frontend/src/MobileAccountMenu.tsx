import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ChevronRight,
  LogOut,
  Moon,
  Settings,
  Shield,
  Sun,
  UserRound,
  X,
} from "lucide-react";
import { displayName } from "./api";
import { Avatar } from "./components";
import type { UserSummary } from "./types";

const AccountMenuContext = createContext<{
  currentUser: UserSummary;
  open: boolean;
  dialogId: string;
  show: () => void;
} | null>(null);

/** Shared by the feed headers; the menu itself stays mounted across routes. */
export function MobileAccountProvider({
  currentUser,
  theme,
  onToggleTheme,
  onLogout,
  children,
}: {
  currentUser: UserSummary;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const dialogId = useId();
  const location = useLocation();
  const close = () => setOpen(false);
  const profileUrl = `/${encodeURIComponent(currentUser.username)}`;

  useEffect(() => setOpen(false), [location.key]);

  useEffect(() => {
    if (!open) return;
    const element = dialog.current!;
    element.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // A modal opened in portrait must not trap focus when the desktop rail appears.
    const desktop = window.matchMedia("(min-width: 721px)");
    const onResize = () => {
      if (desktop.matches) setOpen(false);
    };
    desktop.addEventListener("change", onResize);
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      desktop.removeEventListener("change", onResize);
    };
  }, [open]);

  return (
    <AccountMenuContext.Provider
      value={{ currentUser, open, dialogId, show: () => setOpen(true) }}
    >
      {children}
      <dialog
        id={dialogId}
        className="mobile-account-menu"
        ref={dialog}
        aria-label="Account menu"
        onCancel={close}
        onClose={close}
        onClick={(event) => {
          if (event.target === event.currentTarget) close();
        }}
      >
        <div className="mobile-menu-inner">
          <div className="mobile-menu-heading">
            <span>Account</span>
            <button
              type="button"
              className="icon-button"
              autoFocus
              aria-label="Close account menu"
              onClick={close}
            >
              <X size={19} aria-hidden="true" />
            </button>
          </div>
          <Link className="mobile-account-card" to={profileUrl} onClick={close}>
            <Avatar user={currentUser} />
            <span>
              <strong>{displayName(currentUser)}</strong>
              <small>@{currentUser.username}</small>
            </span>
            <ChevronRight size={18} aria-hidden="true" />
          </Link>
          <nav className="mobile-menu-links" aria-label="Account">
            <Link to={profileUrl} onClick={close}>
              <UserRound size={20} aria-hidden="true" />
              <span>My profile</span>
              <ChevronRight size={16} aria-hidden="true" />
            </Link>
            <Link to="/settings" onClick={close}>
              <Settings size={20} aria-hidden="true" />
              <span>Settings</span>
              <ChevronRight size={16} aria-hidden="true" />
            </Link>
            {currentUser.is_moderator ? (
              <Link to="/moderation" onClick={close}>
                <Shield size={20} aria-hidden="true" />
                <span>Moderation</span>
                <ChevronRight size={16} aria-hidden="true" />
              </Link>
            ) : null}
          </nav>
          <button
            type="button"
            className="mobile-menu-action mobile-appearance"
            onClick={onToggleTheme}
          >
            {theme === "dark" ? (
              <Sun size={19} aria-hidden="true" />
            ) : (
              <Moon size={19} aria-hidden="true" />
            )}
            <span>Appearance</span>
            <small>{theme === "dark" ? "Dark" : "Light"}</small>
            <ChevronRight size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="mobile-menu-action"
            onClick={() => {
              close();
              onLogout();
            }}
          >
            <LogOut size={19} aria-hidden="true" />
            <span>Log out</span>
          </button>
        </div>
      </dialog>
    </AccountMenuContext.Provider>
  );
}

export function FeedHeader({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const menu = useContext(AccountMenuContext);
  return (
    <header
      className={`feed-header${menu ? " feed-header--account" : ""}${className ? ` ${className}` : ""}`}
    >
      {menu ? (
        <button
          type="button"
          className="mobile-account-trigger"
          aria-label="Open account menu"
          aria-haspopup="dialog"
          aria-controls={menu.dialogId}
          aria-expanded={menu.open}
          onClick={menu.show}
        >
          <Avatar user={menu.currentUser} size="small" />
        </button>
      ) : null}
      {children}
    </header>
  );
}
