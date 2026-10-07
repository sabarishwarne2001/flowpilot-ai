/**
 * Password reset request page for FlowPilot AI.
 *
 * The screen shows the SAME confirmation whether or not the address matches an
 * account. The backend answers 202 either way for that reason; showing "no
 * such account" here would rebuild the membership oracle it was written to
 * avoid, and a UI that leaks it is exactly as bad as an API that does.
 *
 * The confirmation is shown even on a network error, because the request may
 * well have reached the server. Telling the user to check their inbox and
 * try again if nothing arrives is honest; telling them it failed when it may
 * not have is not.
 */

import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, KeyRound, Loader2, Mail, MailCheck } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ROUTES } from "@/constants/routes";
import { AuthShell } from "@/components/auth/AuthShell";
import {
  AUTH_INPUT,
  AUTH_INPUT_ICON,
  AUTH_LABEL,
  AUTH_MUTED_LINK,
  AUTH_PRIMARY,
  AUTH_SUBTITLE,
  AUTH_TITLE,
} from "@/components/auth/authStyles";

export function ForgotPassword() {
  const [email, setEmail] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const [sent, setSent] = React.useState(false);

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    if (!email.trim() || sending) {
      return;
    }

    setSending(true);
    try {
      await authApi.forgotPasswordRequest(email.trim());
    } catch {
      // Deliberately swallowed. See the module docstring.
    } finally {
      setSending(false);
      setSent(true);
    }
  };

  const back = (
    <Link to={ROUTES.LOGIN} className={`inline-flex items-center gap-1.5 ${AUTH_MUTED_LINK}`}>
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
      Back to sign in
    </Link>
  );

  if (sent) {
    return (
      <AuthShell>
        <div className="flex flex-col items-center gap-4 py-2 text-center animate-fade-in">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-500/25 bg-emerald-500/10">
            <MailCheck className="h-6 w-6 text-emerald-500" aria-hidden="true" />
          </div>
          <h1 className={AUTH_TITLE}>Check your inbox</h1>
          <p className={`max-w-sm ${AUTH_SUBTITLE}`}>
            If an account exists for <strong className="font-medium text-foreground">{email.trim()}</strong>, a
            reset link is on its way. It expires in an hour and can be used once.
          </p>
          <div className="pt-2">{back}</div>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-muted/60 text-primary shadow-inner-highlight">
        <KeyRound className="h-5 w-5" aria-hidden="true" />
      </div>
      <div className="mb-6 space-y-1.5">
        <h1 className={AUTH_TITLE}>Reset your password</h1>
        <p className={AUTH_SUBTITLE}>
          Enter your email address and we will send you a link to choose a new password.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="forgot-email" className={AUTH_LABEL}>
            Email
          </label>
          <div className="relative">
            <Mail className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              id="forgot-email"
              type="email"
              required
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              className={`${AUTH_INPUT} pl-9`}
            />
          </div>
        </div>
        <button type="submit" disabled={sending} className={AUTH_PRIMARY}>
          {sending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Sending…
            </>
          ) : (
            "Send reset link"
          )}
        </button>
      </form>

      <div className="mt-6 flex justify-center border-t border-border/70 pt-5">{back}</div>
    </AuthShell>
  );
}

export default ForgotPassword;
