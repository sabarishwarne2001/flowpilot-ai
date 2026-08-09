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
import { MailCheck } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ROUTES } from "@/constants/routes";

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

  if (sent) {
    return (
      <Shell>
        <MailCheck className="h-8 w-8 text-emerald-500" />
        <h1 className="text-lg font-semibold">Check your inbox</h1>
        <p className="max-w-sm text-center text-sm text-muted-foreground">
          If an account exists for <strong>{email.trim()}</strong>, a reset link
          is on its way. It expires in an hour and can be used once.
        </p>
        <Link
          to={ROUTES.LOGIN}
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          Back to sign in
        </Link>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-lg font-semibold">Reset your password</h1>
      <p className="max-w-sm text-center text-sm text-muted-foreground">
        Enter your email address and we will send you a link to choose a new
        password.
      </p>

      <form onSubmit={submit} className="w-full max-w-sm space-y-3">
        <input
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={sending}
          className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {sending ? "Sending…" : "Send reset link"}
        </button>
      </form>

      <Link
        to={ROUTES.LOGIN}
        className="text-sm text-muted-foreground underline-offset-4 hover:underline"
      >
        Back to sign in
      </Link>
    </Shell>
  );
}

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex min-h-screen w-full flex-col items-center justify-center gap-4 bg-background px-4">
    {children}
  </div>
);

export default ForgotPassword;
