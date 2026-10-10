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
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";
import { AuthShell } from "@/components/auth/AuthShell";

type Phase = "working" | "verified" | "failed" | "missing";

/**
 * Reads the token from the fragment and removes it from the address bar.
 *
 * Cleared immediately rather than on unmount: until it is gone the token sits
 * in the URL, which means browser history, the tab title in some screen
 * readers, and anything the user copies out of the address bar to paste into a
 * support ticket.
 */
const takeTokenFromFragment = (): { token: string | null; redirect: string | null } => {
  const fragment = window.location.hash.replace(/^#/, "");
  if (!fragment) {
    return { token: null, redirect: null };
  }

  const params = new URLSearchParams(fragment);
  const token = params.get("token");
  // F-225. The server puts where sign-up began beside the token
  // (/verify-email#token=…&redirect=…, validated there); it is read before the
  // fragment is cleared and validated again here, so sign-in can return to it.
  const requested = params.get("redirect");
  const redirect = requested && isSafeRedirectPath(requested) ? requested : null;
  if (token) {
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }
  return { token, redirect };
};

export function VerifyEmail() {
  const navigate = useNavigate();
  const [phase, setPhase] = React.useState<Phase>("working");
  const [message, setMessage] = React.useState<string>("");
  const [redirect, setRedirect] = React.useState<string | null>(null);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  // A ref, not state. React 18 StrictMode mounts effects twice in
  // development, and the token is single-use — the second call would consume
  // nothing and render a failure over a verification that actually succeeded.
  const attempted = React.useRef(false);

  React.useEffect(() => {
    if (attempted.current) {
      return;
    }
    attempted.current = true;

    const { token, redirect: destination } = takeTokenFromFragment();
    setRedirect(destination);
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
        <h1 className="text-[22px] font-semibold tracking-tight">Email verified</h1>
        <p className="text-sm text-muted-foreground">
          Your address is confirmed. You now have full access.
        </p>
        <button
          type="button"
          onClick={() => {
            if (redirect && isAuthenticated) {
              navigate(redirect, { replace: true });
            } else if (redirect) {
              navigate(`${ROUTES.LOGIN}?redirect=${encodeURIComponent(redirect)}`, { replace: true });
            } else {
              navigate(ROUTES.LOGIN, { replace: true });
            }
          }}
          className="fp-btn fp-btn-primary h-10 px-5"
        >
          Continue
        </button>
      </Shell>
    );
  }

  return (
    <Shell>
      <XCircle className="h-8 w-8 text-destructive" />
      <h1 className="text-[22px] font-semibold tracking-tight">
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
        className="fp-btn fp-btn-primary h-10 px-5"
      >
        Go to sign in
      </Link>
    </Shell>
  );
}

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AuthShell>
    <div className="flex flex-col items-center gap-4 py-2 text-center animate-fade-in">{children}</div>
  </AuthShell>
);

export default VerifyEmail;
