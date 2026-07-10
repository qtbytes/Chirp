import { FormEvent, useState } from "react";
import { X } from "lucide-react";
import { changePassword } from "./api";
import { getErrorMessage } from "./components";

// Mirrors PASSWORD_MIN_LENGTH in backend/app/schemas/user.py. Checked here only
// to turn a certain 422 into an inline message; the server still decides.
const PASSWORD_MIN_LENGTH = 8;

export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (next.length < PASSWORD_MIN_LENGTH) {
      setError(
        `New password must be at least ${PASSWORD_MIN_LENGTH} characters.`,
      );
      return;
    }
    if (next !== confirm) {
      setError("The two new passwords do not match.");
      return;
    }

    setSaving(true);
    try {
      await changePassword(current, next);
      setDone(true);
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
        aria-labelledby="change-password-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} aria-hidden="true" />
          </button>
          <h2 id="change-password-title">Change password</h2>
          {done ? (
            <button className="primary-button compact" onClick={onClose}>
              Done
            </button>
          ) : (
            <button
              className="primary-button compact"
              form="change-password-form"
              disabled={saving}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </header>

        {done ? (
          <div className="modal-body">
            <p>Password updated.</p>
            {/* The side effect is not obvious, so say it rather than let the
                user discover it on their phone. */}
            <p className="form-hint">
              Every other device has been signed out. This one stays signed in.
            </p>
          </div>
        ) : (
          <form
            id="change-password-form"
            onSubmit={handleSubmit}
            className="modal-body"
          >
            <label className="edit-field">
              <span>Current password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
                required
              />
            </label>
            <label className="edit-field">
              <span>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
                minLength={PASSWORD_MIN_LENGTH}
                required
              />
            </label>
            <label className="edit-field">
              <span>Confirm new password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                required
              />
            </label>
            <p className="form-hint">
              Changing your password signs out every other device.
            </p>
            {error ? <p className="form-error">{error}</p> : null}
          </form>
        )}
      </section>
    </div>
  );
}
