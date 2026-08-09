/**
 * Password reset completion page for FlowPilot AI.
 *
 * The token arrives in the URL FRAGMENT (ARCH-03 §B.9) and is cleared from the
 * address bar as soon as it is read. A reset link is a password-equivalent
 * credential; leaving it in the URL puts it in browser history and in whatever
 * a confused user copies into a support ticket.
 *
 * Public. The link opens from a mail client, usually in a signed-out browser.
 *
 * No session is issued on success — completing a reset does not sign you in.
 * The user is sent to the login page to use the password they just chose.
 */

import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { CheckCircle2, XCircle } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";
import { useAuthStore } from "@/store/useAuthStore";

const MIN_PASSWORD_LENGTH = 8;

/**
 * Reads the token from the fragment and strips it from the address bar.
 */
const takeTokenFromFragment = (): string | null => {
  const fragment = window.location.hash.replace(/^#/, "");
  if (!fragment) {
    return null;
  }

  const token = new URLSearchParams(fragment).get("token");
  if (token) {
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }
  return token;
};

export function ResetPassword() {
  const navigate = useNavigate();

  // Captured once, on first render, because the effect that clears the
  // fragment would otherwise leave nothing to read on a re-render.
  const [token] = React.useState<string | null>(takeTokenFromFragment);

  const [password, setPassword] = React.useState("");
  const [confirmation, setConfirmation] = React.useState("");
  const [error, setError] = React.useState("");
  const [working, setWorking] = React.useState(false);
  const [done, setDone] = React.useState(false);

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmation) {
      setError("The two passwords do not match.");
      return;
    }

    setError("");
    setWorking(true);
    try {
      await authApi.resetPasswordRequest(token as string, password);
      // Every session was revoked server-side, including anything this
      // browser held. Clearing locally keeps the two in agreement.
      useAuthStore.getState().clearAuth();
      setDone(true);
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "We could not reset your password.",
      );
    } finally {
      setWorking(false);
    }
  };

  if (!token) {
    return (
      <Shell>
        <XCircle className="h-8 w-8 text-destructive" />
        <h1 className="text-lg font-semibold">Nothing to reset</h1>
        <p className="max-w-sm text-center text-sm text-muted-foreground">
          This page needs a reset link. Request one and open the email we send.
        </p>
        <Link
          to={ROUTES.FORGOT_PASSWORD}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Request a link
        </Link>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <CheckCircle2 className="h-8 w-8 text-emerald-500" />
        <h1 className="text-lg font-semibold">Password updated</h1>
        <p className="max-w-sm text-center text-sm text-muted-foreground">
          Every device has been signed out. Sign in with your new password.
        </p>
        <button
          type="button"
          onClick={() => navigate(ROUTES.LOGIN, { replace: true })}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Go to sign in
        </button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-lg font-semibold">Choose a new password</h1>

      <form onSubmit={submit} className="w-full max-w-sm space-y-3">
        <input
          type="password"
          required
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="New password"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />
        <input
          type="password"
          required
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          placeholder="Confirm new password"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />

        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={working}
          className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {working ? "Updating…" : "Update password"}
        </button>
      </form>

      <p className="max-w-sm text-center text-xs text-muted-foreground">
        Updating your password signs you out everywhere.
      </p>
    </Shell>
  );
}

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex min-h-screen w-full flex-col items-center justify-center gap-4 bg-background px-4">
    {children}
  </div>
);

export default ResetPassword;
