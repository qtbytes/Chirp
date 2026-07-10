import { FormEvent, useState } from "react";
import { X } from "lucide-react";
import { forgotPassword } from "./api";
import { getErrorMessage } from "./components";

export function ForgotPasswordModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="forgot-password-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} aria-hidden="true" />
          </button>
          <h2 id="forgot-password-title">Reset password</h2>
          {sent ? (
            <button className="primary-button compact" onClick={onClose}>
              Done
            </button>
          ) : (
            <button
              className="primary-button compact"
              form="forgot-password-form"
              disabled={submitting}
            >
              {submitting ? "Sending..." : "Send link"}
            </button>
          )}
        </header>

        {sent ? (
          <div className="modal-body">
            {/* Says "if" on purpose. The server answers the same whether or not
                the address has an account, and this copy must not undo that by
                confirming one exists. */}
            <p>If that address has a confirmed Chirp account, a reset link is on its way.</p>
            <p className="form-hint">The link works once and expires in 30 minutes.</p>
          </div>
        ) : (
          <form id="forgot-password-form" onSubmit={handleSubmit} className="modal-body">
            <label className="edit-field">
              <span>Email address</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
            <p className="form-hint">
              We'll email you a link to choose a new password.
            </p>
            {error ? <p className="form-error">{error}</p> : null}
          </form>
        )}
      </section>
    </div>
  );
}
