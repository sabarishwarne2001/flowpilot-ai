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
import { AlertCircle, CheckCircle2, Loader2, Lock, ShieldCheck, XCircle } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";
import { useAuthStore } from "@/store/useAuthStore";
import { MIN_PASSWORD_LENGTH } from "@/utils/validation";
import PasswordStrengthMeter from "@/components/auth/PasswordStrengthMeter";
import { AuthShell } from "@/components/auth/AuthShell";
import {
  AUTH_ERROR,
  AUTH_INPUT,
  AUTH_INPUT_ICON,
  AUTH_LABEL,
  AUTH_PRIMARY,
  AUTH_SUBTITLE,
  AUTH_TITLE,
} from "@/components/auth/authStyles";

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
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
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
  const [showPasswords, setShowPasswords] = React.useState(false);
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
      setError(caught instanceof ApiError ? caught.message : "We could not reset your password.");
    } finally {
      setWorking(false);
    }
  };

  if (!token) {
    return (
      <Shell>
        <Status icon={<XCircle className="h-6 w-6 text-destructive" aria-hidden="true" />} tone="border-destructive/25 bg-destructive/10">
          <h1 className={AUTH_TITLE}>Nothing to reset</h1>
          <p className={`max-w-sm ${AUTH_SUBTITLE}`}>
            This page needs a reset link. Request one and open the email we send.
          </p>
          <Link to={ROUTES.FORGOT_PASSWORD} className={`${AUTH_PRIMARY} mt-2`}>
            Request a link
          </Link>
        </Status>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <Status icon={<CheckCircle2 className="h-6 w-6 text-emerald-500" aria-hidden="true" />} tone="border-emerald-500/25 bg-emerald-500/10">
          <h1 className={AUTH_TITLE}>Password updated</h1>
          <p className={`max-w-sm ${AUTH_SUBTITLE}`}>
            Every device has been signed out. Sign in with your new password.
          </p>
          <button
            type="button"
            onClick={() => navigate(ROUTES.LOGIN, { replace: true })}
            className={`${AUTH_PRIMARY} mt-2`}
          >
            Go to sign in
          </button>
        </Status>
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-muted/60 text-primary shadow-inner-highlight">
        <ShieldCheck className="h-5 w-5" aria-hidden="true" />
      </div>
      <div className="mb-6 space-y-1.5">
        <h1 className={AUTH_TITLE}>Choose a new password</h1>
        <p className={AUTH_SUBTITLE}>Updating your password signs you out everywhere.</p>
      </div>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <span className={AUTH_LABEL} aria-hidden="true">
            New password
          </span>
          <div className="relative">
            <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              type={showPasswords ? "text" : "password"}
              required
              autoComplete="new-password"
              aria-label="New password"
              autoFocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="New password"
              className={`${AUTH_INPUT} pl-9`}
            />
          </div>
          <PasswordStrengthMeter password={password} />
        </div>
        <div className="space-y-1.5">
          <span className={AUTH_LABEL} aria-hidden="true">
            Confirm new password
          </span>
          <div className="relative">
            <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              type={showPasswords ? "text" : "password"}
              required
              autoComplete="new-password"
              aria-label="Confirm new password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              placeholder="Confirm new password"
              className={`${AUTH_INPUT} pl-9`}
            />
          </div>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-muted-foreground select-none">
          <input
            type="checkbox"
            checked={showPasswords}
            onChange={(event) => setShowPasswords(event.target.checked)}
            className="h-4 w-4 rounded border-border accent-[hsl(var(--primary))]"
          />
          Show passwords
        </label>

        {error ? (
          <div className={AUTH_ERROR} role="alert">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        ) : null}

        <button type="submit" disabled={working} className={AUTH_PRIMARY}>
          {working ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Updating…
            </>
          ) : (
            "Update password"
          )}
        </button>
      </form>
    </Shell>
  );
}

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => <AuthShell>{children}</AuthShell>;

const Status: React.FC<{ icon: React.ReactNode; tone: string; children: React.ReactNode }> = ({ icon, tone, children }) => (
  <div className="flex flex-col items-center gap-4 py-2 text-center animate-fade-in">
    <div className={`flex h-12 w-12 items-center justify-center rounded-2xl border ${tone}`}>{icon}</div>
    {children}
  </div>
);

export default ResetPassword;
