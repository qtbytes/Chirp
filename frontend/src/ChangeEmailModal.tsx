import { FormEvent, useState } from "react";
import { X } from "lucide-react";
import { changeEmail, resendVerification } from "./api";
import { getErrorMessage } from "./components";
import type { UserProfile } from "./types";

export function ChangeEmailModal({
  profile,
  onClose,
  onChanged,
}: {
  profile: UserProfile;
  onClose: () => void;
  onChanged: (pendingEmail: string) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [email, setEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const { pending_email } = await changeEmail(currentPassword, email.trim());
      onChanged(pending_email);
      setSentTo(pending_email);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleResend() {
    setSaving(true);
    setError("");
    try {
      const { pending_email } = await resendVerification();
      setSentTo(pending_email);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="change-email-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} aria-hidden="true" />
          </button>
          <h2 id="change-email-title">Email</h2>
          {sentTo ? (
            <button className="primary-button compact" onClick={onClose}>
              Done
            </button>
          ) : (
            <button
              className="primary-button compact"
              form="change-email-form"
              disabled={saving}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </header>

        {sentTo ? (
          <div className="modal-body">
            <p>
              A confirmation link is on its way to <strong>{sentTo}</strong>.
            </p>
            {/* The confirmed address does not move until the link is clicked --
                that is what stops a thief from silently diverting reset mail. */}
            <p className="form-hint">
              Your current address keeps receiving password resets until the new
              one is confirmed.
            </p>
          </div>
        ) : (
          <form id="change-email-form" onSubmit={handleSubmit} className="modal-body">
            <p className="form-hint">
              {profile.email
                ? `Confirmed: ${profile.email}`
                : "You have no confirmed address yet, so you cannot reset a forgotten password."}
            </p>

            {profile.pending_email ? (
              <p className="form-hint">
                Awaiting confirmation: <strong>{profile.pending_email}</strong>{" "}
                <button
                  type="button"
                  className="text-button inline"
                  onClick={() => void handleResend()}
                  disabled={saving}
                >
                  Resend link
                </button>
              </p>
            ) : null}

            <label className="edit-field">
              <span>New email address</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                maxLength={254}
                required
              />
            </label>
            <label className="edit-field">
              <span>Current password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                required
              />
            </label>
            <p className="form-hint">
              Your password is required: it is what stops someone who has stolen
              a session from redirecting your reset emails.
            </p>
            {error ? <p className="form-error">{error}</p> : null}
          </form>
        )}
      </section>
    </div>
  );
}
