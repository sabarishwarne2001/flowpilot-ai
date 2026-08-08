/**
 * Email verification landing page for FlowPilot AI.
 *
 * The token arrives in the URL FRAGMENT, not the query string (ARCH-03 §B.9).
 * A fragment is never transmitted to any server, so the token cannot leak
 * through the Referer header to third-party assets on this page and cannot be
 * written to a proxy or web-server access log. This component reads it,
 * immediately clears it from the address bar, and POSTs it in a request body.
 *
 * Public by design. The link opens from a mail client in whatever browser is
 * default, which is usually one without a session. Requiring authentication
 * here would strand the majority of recipients.
 */

import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";
import { useAuthStore } from "@/store/useAuthStore";

type Phase = "working" | "verified" | "failed" | "missing";

/**
 * Reads the token from the fragment and removes it from the address bar.
 *
 * Cleared immediately rather than on unmount: until it is gone the token sits
 * in the URL, which means browser history, the tab title in some screen
 * readers, and anything the user copies out of the address bar to paste into a
 * support ticket.
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

export function VerifyEmail() {
  const navigate = useNavigate();
  const [phase, setPhase] = React.useState<Phase>("working");
  const [message, setMessage] = React.useState<string>("");

  // A ref, not state. React 18 StrictMode mounts effects twice in
  // development, and the token is single-use — the second call would consume
  // nothing and render a failure over a verification that actually succeeded.
  const attempted = React.useRef(false);

  React.useEffect(() => {
    if (attempted.current) {
      return;
    }
    attempted.current = true;

    const token = takeTokenFromFragment();
    if (!token) {
      setPhase("missing");
      return;
    }

    void authApi
      .verifyEmailRequest(token)
      .then(() => {
        setPhase("verified");
        // The gate reads the User row, not a token claim, so a signed-in user
        // needs no new token — but the cached user object is now stale.
        useAuthStore.getState().clearUserCache();
      })
      .catch((error: unknown) => {
        setPhase("failed");
        setMessage(
          error instanceof ApiError
            ? error.message
            : "We could not verify this link.",
        );
      });
  }, []);

  if (phase === "working") {
    return (
      <Shell>
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Verifying your email…</p>
      </Shell>
    );
  }

  if (phase === "verified") {
    return (
      <Shell>
        <CheckCircle2 className="h-8 w-8 text-emerald-500" />
        <h1 className="text-lg font-semibold">Email verified</h1>
        <p className="text-sm text-muted-foreground">
          Your address is confirmed. You now have full access.
        </p>
        <button
          type="button"
          onClick={() => navigate(ROUTES.LOGIN, { replace: true })}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Continue
        </button>
      </Shell>
    );
  }

  return (
    <Shell>
      <XCircle className="h-8 w-8 text-destructive" />
      <h1 className="text-lg font-semibold">
        {phase === "missing" ? "Nothing to verify" : "Verification failed"}
      </h1>
      <p className="max-w-sm text-center text-sm text-muted-foreground">
        {phase === "missing"
          ? "This page needs a verification link. Open the one we emailed you."
          : message}
      </p>
      <p className="text-sm text-muted-foreground">
        Sign in and request a new link from the banner at the top of the page.
      </p>
      <Link
        to={ROUTES.LOGIN}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
      >
        Go to sign in
      </Link>
    </Shell>
  );
}

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex min-h-screen w-full flex-col items-center justify-center gap-4 bg-background px-4">
    {children}
  </div>
);

export default VerifyEmail;
