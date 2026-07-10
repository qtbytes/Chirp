/**
 * The two pages a mailed link lands on.
 *
 * Both must live outside the app's auth gate: whoever opens a reset link is by
 * definition unable to log in, and a confirmation link is often opened in a
 * browser that has never signed in at all.
 */

import { FormEvent, useEffect, useRef, useState } from "react";
import { Feather, Loader2 } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { resetPassword, verifyEmail } from "./api";
import { getErrorMessage } from "./components";

// Mirrors PASSWORD_MIN_LENGTH in backend/app/schemas/user.py.
const PASSWORD_MIN_LENGTH = 8;

function AuthPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="token-view-title">
        <div className="brand-mark">
          <Feather size={30} aria-hidden="true" />
        </div>
        <h1 id="token-view-title">{title}</h1>
        {children}
      </section>
    </main>
  );
}

export function ResetPasswordView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (password.length < PASSWORD_MIN_LENGTH) {
      setError(`Password must be at least ${PASSWORD_MIN_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthPanel title="Reset password">
        <p className="form-error">This link is missing its token.</p>
        <Link className="text-button" to="/">
          Back to Chirp
        </Link>
      </AuthPanel>
    );
  }

  if (done) {
    return (
      <AuthPanel title="Password updated">
        {/* The server deliberately does not sign the caller in: whoever holds
            the link may be whoever read the mailbox. Say so plainly. */}
        <p className="form-hint">
          Every device has been signed out. Log in with your new password.
        </p>
        <button className="primary-button" onClick={() => navigate("/")}>
          Go to log in
        </button>
      </AuthPanel>
    );
  }

  return (
    <AuthPanel title="Choose a new password">
      <form onSubmit={handleSubmit} className="auth-form">
        <label>
          <span>New password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            minLength={PASSWORD_MIN_LENGTH}
            maxLength={128}
            required
          />
        </label>
        <label>
          <span>Confirm new password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            required
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button className="primary-button" disabled={submitting}>
          {submitting ? "Working..." : "Reset password"}
        </button>
      </form>
    </AuthPanel>
  );
}

export function VerifyEmailView() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  const [error, setError] = useState("");

  // React 18+ mounts effects twice in StrictMode. The token is single-use, so a
  // second POST would report "invalid or expired" for a link that just worked.
  const redeemed = useRef(false);

  useEffect(() => {
    if (!token) {
      setState("failed");
      setError("This link is missing its token.");
      return;
    }
    if (redeemed.current) return;
    redeemed.current = true;

    verifyEmail(token)
      .then(() => setState("done"))
      .catch((err) => {
        setError(getErrorMessage(err));
        setState("failed");
      });
  }, [token]);

  if (state === "working") {
    return (
      <AuthPanel title="Confirming your email">
        <Loader2 className="spin" aria-hidden="true" />
      </AuthPanel>
    );
  }

  return (
    <AuthPanel title={state === "done" ? "Email confirmed" : "Could not confirm"}>
      {state === "done" ? (
        <p className="form-hint">
          You can now reset your password by email if you ever lose it.
        </p>
      ) : (
        <p className="form-error">{error}</p>
      )}
      <Link className="text-button" to="/">
        Back to Chirp
      </Link>
    </AuthPanel>
  );
}
