import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { KeyRound, Loader2, Lock, Mail } from "lucide-react";

import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ROUTES } from "@/constants/routes";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { discoverSso, emailDomain, ssoStartHref } from "@/services/api/sso";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * Sign-in page: password, or enterprise single sign-on.
 *
 * ARCH-30 Tranche 1 (T4-F4). The backend has had SSO discovery, SAML ACS and
 * OIDC callback since ARCH-16, and Step 0 made both federated paths set the
 * refresh cookie. This page had no way to reach any of it: an employee of a
 * customer with Okta configured saw an email-and-password form and no other
 * door.
 *
 * WHY AN EXPLICIT MODE AND NOT AUTO-DETECTION ON TYPING
 * =====================================================
 *
 * Discovering on every keystroke (or on blur) would call an unauthenticated
 * endpoint that reveals which email domains use SSO, once per visitor per
 * domain typed, including by people who only meant to use a password. An
 * explicit "Continue with single sign-on" keeps that disclosure to users who
 * asked for it, and keeps the password path exactly as it was.
 *
 * WHAT HAPPENS ON "CONTINUE" IN SSO MODE
 * ======================================
 *
 *   1. The domain is taken from the email (`emailDomain`).
 *   2. `/sso/discover` answers whether an active IdP is bound to it.
 *   3. If so, the browser navigates to `/sso/start`, which 302s to the IdP.
 *      The IdP returns to the backend, the backend sets the refresh cookie and
 *      302s to `/auth/sso/complete`, and `SsoComplete` finishes the session.
 *   4. If not, the page says so and offers the password form with the email
 *      already filled in.
 */

type SignInMode = "password" | "sso";

const INPUT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 pl-10 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

const LABEL_CLASS =
  "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70";

const PRIMARY_BUTTON_CLASS =
  "inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 w-full";

const SECONDARY_BUTTON_CLASS =
  "inline-flex items-center justify-center gap-2 rounded-md border border-input bg-background text-sm font-medium ring-offset-background transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 h-10 px-4 py-2 w-full";

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  /**
   * Where to land after a successful sign-in.
   *
   * ARCH-05 Step 0.5 companion. Every guard in the application builds
   * `/login?redirect=…` — loginPathWithRedirect exists for exactly that — and
   * this page ignored the parameter entirely, sending everyone to the
   * workspace picker. Session expiry on a deep link therefore lost the
   * destination, which is half of the defect ARCH-01 set out to fix.
   *
   * It became load-bearing at Step 0.5: an invitee arriving without a session
   * is the majority case for invitation acceptance, and without this they sign
   * in, land on the picker, and never return to the invitation they were
   * holding.
   *
   * ARCH-30 Tranche 1: the same value is carried through the IdP round trip as
   * `redirect_path`, so an invitee whose company uses SSO returns to the
   * invitation too.
   *
   * Validated with isSafeRedirectPath rather than a local allowlist. That
   * function is the one tenantPaths self-checks, it rejects protocol-relative
   * and scheme-bearing values, and a second copy of open-redirect logic is a
   * second place for it to be wrong.
   */
  const redirectTo = (() => {
    const requested = new URLSearchParams(location.search).get("redirect");
    return requested && isSafeRedirectPath(requested)
      ? requested
      : `${ROUTES.WORKSPACES}?landing=1`;
  })();

  const [mode, setMode] = useState<SignInMode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const setToken = useAuthStore((state) => state.setToken);
  const setAuth = useAuthStore((state) => state.setAuth);

  /**
   * The email survives a mode switch, because the most common reason to
   * switch is "SSO isn't set up for this address, use your password". The
   * password does not: it should never sit in state while the SSO form, which
   * has no password field, is on screen.
   */
  const switchMode = (next: SignInMode) => {
    setMode(next);
    setError(null);
    setNotice(null);
    setPassword("");
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password || isLoading) {
      return;
    }

    setIsLoading(true);
    setError(null);
    setNotice(null);

    try {
      // 1. Authenticate and receive the JWT token
      const tokenResponse = await authApi.loginRequest({ email, password });

      // 2. Set the token locally so the /auth/me request can authenticate
      setToken(tokenResponse.access_token);

      // 3. Resolve the current user profile
      const userResponse = await authApi.getMeRequest();

      // 4. Commit the authenticated session
      setAuth(userResponse, tokenResponse.access_token);

      // 5. Shift viewport to the requested destination, or the picker.
      //    `replace` so the back button does not return to a login form the
      //    user has already satisfied.
      navigate(redirectTo, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === API_ERROR_CODES.UNAUTHORIZED) {
          setError("Incorrect email or password.");
        } else {
          setError(err.message);
        }
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleSsoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isLoading) {
      return;
    }

    setError(null);
    setNotice(null);

    const domain = emailDomain(email);
    if (!domain) {
      setError("Enter your full work email address, for example alex@acme.com.");
      return;
    }

    setIsLoading(true);

    // True once the browser is navigating to the IdP. The spinner must stay
    // up until the page unloads; clearing it in `finally` would re-enable the
    // button for the second or two before the navigation commits, and a second
    // click creates a second SSO auth request.
    let leavingPage = false;

    try {
      const discovery = await discoverSso(domain);
      if (discovery.sso_enabled) {
        leavingPage = true;
        window.location.assign(ssoStartHref(domain, redirectTo));
        return;
      }
      setNotice(
        `Single sign-on isn't set up for ${domain}. Sign in with your password instead.`,
      );
    } catch (err) {
      // A 409 means two organizations bind this domain; the backend's message
      // says to contact an administrator, which is the only useful advice.
      setError(
        err instanceof ApiError
          ? err.message
          : "We couldn't check single sign-on for that address. Please try again.",
      );
    } finally {
      if (!leavingPage) {
        setIsLoading(false);
      }
    }
  };

  const feedback = (
    <>
      {error && (
        <div
          className="rounded-md bg-destructive/15 p-3 text-sm text-destructive"
          role="alert"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          className="rounded-md border border-border bg-muted/50 p-3 text-sm text-foreground"
          role="status"
        >
          <p>{notice}</p>
          <button
            type="button"
            onClick={() => switchMode("password")}
            className="mt-2 text-sm font-medium underline underline-offset-4 hover:text-primary"
          >
            Use password
          </button>
        </div>
      )}
    </>
  );

  const emailField = (
    <div className="space-y-2">
      <label className={LABEL_CLASS} htmlFor="email">
        {mode === "sso" ? "Work email" : "Email"}
      </label>
      <div className="relative">
        <Mail className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
        <input
          id="email"
          placeholder={mode === "sso" ? "alex@company.com" : "name@example.com"}
          type="email"
          autoCapitalize="none"
          autoComplete="email"
          autoCorrect="off"
          disabled={isLoading}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={INPUT_CLASS}
          required
        />
      </div>
    </div>
  );

  if (mode === "sso") {
    return (
      <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[350px]">
        <div className="flex flex-col space-y-2 text-center select-none">
          <h1 className="text-2xl font-semibold tracking-tight">
            Sign in with single sign-on
          </h1>
          <p className="text-sm text-muted-foreground">
            Enter your work email and we&apos;ll send you to your company&apos;s
            sign-in page
          </p>
        </div>

        <form onSubmit={handleSsoSubmit} className="space-y-4">
          {feedback}
          {emailField}

          <button type="submit" disabled={isLoading} className={PRIMARY_BUTTON_CLASS}>
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Redirecting...
              </>
            ) : (
              "Continue"
            )}
          </button>

          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => switchMode("password")}
              disabled={isLoading}
              className="text-sm text-muted-foreground underline-offset-4 hover:underline disabled:opacity-50"
            >
              Sign in with a password instead
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[350px]">
      <div className="flex flex-col space-y-2 text-center select-none">
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome back
        </h1>
        <p className="text-sm text-muted-foreground">
          Enter your email to sign in to your account
        </p>
      </div>

      <form onSubmit={handlePasswordSubmit} className="space-y-4">
        {feedback}
        {emailField}

        <div className="space-y-2">
          <label className={LABEL_CLASS} htmlFor="password">
            Password
          </label>
          <div className="relative">
            <Lock className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              id="password"
              placeholder="••••••••"
              type="password"
              autoComplete="current-password"
              disabled={isLoading}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT_CLASS}
              required
            />
          </div>
        </div>

        <button type="submit" disabled={isLoading} className={PRIMARY_BUTTON_CLASS}>
          {isLoading ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Signing in...
            </>
          ) : (
            "Continue"
          )}
        </button>
        <div className="flex justify-end">
          <Link
            to={ROUTES.FORGOT_PASSWORD}
            className="text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            Forgot your password?
          </Link>
        </div>
      </form>

      <div className="relative select-none" aria-hidden="true">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t border-border" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-background px-2 text-muted-foreground">or</span>
        </div>
      </div>

      <button
        type="button"
        onClick={() => switchMode("sso")}
        disabled={isLoading}
        className={SECONDARY_BUTTON_CLASS}
      >
        <KeyRound className="h-4 w-4" />
        Continue with single sign-on
      </button>

      <footer className="pt-2 text-center select-none">
        <p className="text-sm text-muted-foreground">
          Don't have an account?{" "}
          <Link
            to={ROUTES.REGISTER}
            className="underline underline-offset-4 hover:text-primary transition-colors"
          >
            Sign up
          </Link>
        </p>
      </footer>
    </div>
  );
};

export default Login;
