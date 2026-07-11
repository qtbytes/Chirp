import { useEffect, useState } from "react";
import { KeyRound, Loader2, Mail } from "lucide-react";
import { getUserProfile } from "./api";
import type { UserProfile, UserSummary } from "./types";
import { getErrorMessage } from "./components";
import { ChangeEmailModal } from "./ChangeEmailModal";
import { ChangePasswordModal } from "./ChangePasswordModal";

export function AccountView({ currentUser }: { currentUser: UserSummary }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [error, setError] = useState("");
  const [changingEmail, setChangingEmail] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setError("");
    getUserProfile(currentUser.username)
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [currentUser.username]);

  return (
    <>
      <header className="feed-header">
        <div className="feed-title-row">
          <h1>Account</h1>
        </div>
      </header>

      {error ? <div className="status-panel error">{error}</div> : null}

      {!profile && !error ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading account</span>
        </div>
      ) : null}

      {profile ? (
        <div className="settings-list">
          <div className="settings-row">
            <span className="settings-row-icon" aria-hidden="true">
              <Mail size={20} />
            </span>
            <div className="settings-row-copy">
              <strong>Email</strong>
              {profile.email ? (
                <span>{profile.email} · confirmed</span>
              ) : profile.pending_email ? (
                <span className="settings-warn">
                  {profile.pending_email} · awaiting confirmation
                </span>
              ) : (
                <span className="settings-warn">
                  Not set — required to reset a forgotten password
                </span>
              )}
            </div>
            <button className="outline-button" onClick={() => setChangingEmail(true)}>
              {profile.email || profile.pending_email ? "Change" : "Add"}
            </button>
          </div>

          <div className="settings-row">
            <span className="settings-row-icon" aria-hidden="true">
              <KeyRound size={20} />
            </span>
            <div className="settings-row-copy">
              <strong>Password</strong>
              <span>Changing it signs out every other device.</span>
            </div>
            <button className="outline-button" onClick={() => setChangingPassword(true)}>
              Change
            </button>
          </div>
        </div>
      ) : null}

      {changingEmail && profile ? (
        <ChangeEmailModal
          profile={profile}
          onClose={() => setChangingEmail(false)}
          onChanged={(pendingEmail) =>
            setProfile({ ...profile, pending_email: pendingEmail })
          }
        />
      ) : null}

      {changingPassword ? (
        <ChangePasswordModal onClose={() => setChangingPassword(false)} />
      ) : null}
    </>
  );
}
