import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, ArrowRight, Check, Eye, EyeOff, KeyRound, Loader2, Lock, Mail, Smartphone } from "lucide-react";

import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ROUTES } from "@/constants/routes";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { discoverSso, emailDomain, ssoStartHref } from "@/services/api/sso";
import { useAuthStore } from "@/store/useAuthStore";
import { isMfaChallenge } from "@/types/auth";
import {
  AUTH_ERROR,
  AUTH_INPUT,
  AUTH_INPUT_ICON,
  AUTH_LABEL,
  AUTH_LINK,
  AUTH_MUTED_LINK,
  AUTH_PRIMARY,
  AUTH_SECONDARY,
  AUTH_SUBTITLE,
  AUTH_TITLE,
  AUTH_TRAILING_BUTTON,
} from "@/components/auth/authStyles";

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

// HARDENING-T3: "identify" is the email-first step (see handleIdentifySubmit).
// N-017: "code" is the second step for a user with two-factor sign-in on.
type SignInMode = "identify" | "password" | "sso" | "code";

/** Where the user is in email -> password -> (two-factor code). */
const Steps: React.FC<{ readonly mode: SignInMode }> = ({ mode }) => {
  const steps = mode === "code" ? ["Email", "Password", "Code"] : ["Email", "Password"];
  const current = mode === "identify" ? 0 : mode === "password" ? 1 : 2;
  return (
    <ol className="mb-6 flex items-center gap-2" aria-label="Sign-in progress">
      {steps.map((label, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={label} className="flex items-center gap-2" aria-current={active ? "step" : undefined}>
            <span
              className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold transition-colors ${
                done
                  ? "bg-primary text-primary-foreground"
                  : active
                    ? "bg-primary/15 text-primary ring-1 ring-primary/40 dark:text-[hsl(213_94%_72%)]"
                    : "bg-muted text-muted-foreground ring-1 ring-border"
              }`}
            >
              {done ? <Check className="h-3 w-3" aria-hidden="true" /> : index + 1}
            </span>
            <span className={`text-xs ${active ? "font-medium text-foreground" : "text-muted-foreground"}`}>{label}</span>
            {index < steps.length - 1 && <span className="h-px w-6 bg-border" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
};

const Heading: React.FC<{ readonly title: string; readonly children: React.ReactNode }> = ({ title, children }) => (
  <div className="mb-6 space-y-1.5 select-none">
    <h1 className={AUTH_TITLE}>{title}</h1>
    <p className={AUTH_SUBTITLE}>{children}</p>
  </div>
);

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

  const [mode, setMode] = useState<SignInMode>("identify");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

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
    setMfaToken(null);
    setCode("");
  };

  const finishSignIn = async (accessToken: string) => {
    // 1. Set the token locally so the /auth/me request can authenticate
    setToken(accessToken);

    // 2. Resolve the current user profile
    const userResponse = await authApi.getMeRequest();

    // 3. Commit the authenticated session
    setAuth(userResponse, accessToken);

    // 4. Shift viewport to the requested destination, or the picker.
    //    `replace` so the back button does not return to a login form the
    //    user has already satisfied.
    navigate(redirectTo, { replace: true });
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
      const loginResponse = await authApi.loginRequest({ email, password });
      if (isMfaChallenge(loginResponse)) {
        // N-017: right password, second step still to come. No session yet.
        setPassword("");
        setMfaToken(loginResponse.mfa_token);
        setMode("code");
        return;
      }
      await finishSignIn(loginResponse.access_token);
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

  const handleCodeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mfaToken || !code.trim() || isLoading) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const tokenResponse = await authApi.loginSecondFactorRequest(mfaToken, code);
      await finishSignIn(tokenResponse.access_token);
    } catch (err) {
      setCode("");
      // The server's words: a wrong or expired code, or too many tries.
      setError(
        err instanceof ApiError ? err.message : "An unexpected error occurred. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  // HARDENING-T3: email-first sign-in. Discovery runs once, on Continue —
  // never per keystroke — so it discloses nothing the explicit SSO button did
  // not already: an SSO-bound domain goes to its IdP, anything else (or a
  // discovery failure) continues to the password step with the email filled.
  const handleIdentifySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isLoading) {
      return;
    }
    setError(null);
    setNotice(null);
    const domain = emailDomain(email);
    if (!domain) {
      setError("Enter your full email address, for example alex@acme.com.");
      return;
    }
    setIsLoading(true);
    let leavingPage = false;
    try {
      const discovery = await discoverSso(domain);
      if (discovery.sso_enabled) {
        leavingPage = true;
        window.location.assign(ssoStartHref(domain, redirectTo));
        return;
      }
      setMode("password");
    } catch {
      setNotice("We couldn't check single sign-on for that address. Sign in with your password.");
      setMode("password");
    } finally {
      if (!leavingPage) {
        setIsLoading(false);
      }
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
      setNotice(`Single sign-on isn't set up for ${domain}. Sign in with your password instead.`);
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
        <div className={AUTH_ERROR} role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div
          className="rounded-lg border border-border bg-muted/50 px-3 py-2.5 text-[13px] text-foreground animate-fade-in"
          role="status"
        >
          <p>{notice}</p>
          <button
            type="button"
            onClick={() => switchMode("password")}
            className={`mt-1.5 text-[13px] ${AUTH_LINK}`}
          >
            Use password
          </button>
        </div>
      )}
    </>
  );

  const emailField = (
    <div className="space-y-1.5">
      <label className={AUTH_LABEL} htmlFor="email">
        {mode === "sso" ? "Work email" : "Email"}
      </label>
      <div className="relative">
        <Mail className={AUTH_INPUT_ICON} aria-hidden="true" />
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
          className={`${AUTH_INPUT} pl-9`}
          required
        />
      </div>
    </div>
  );

  const signUpFooter = (
    <p className="mt-6 border-t border-border/70 pt-5 text-center text-[13px] text-muted-foreground select-none">
      Don't have an account?{" "}
      <Link to={ROUTES.REGISTER} className={AUTH_LINK}>
        Sign up
      </Link>
    </p>
  );

  if (mode === "identify") {
    return (
      <div className="flex w-full flex-col">
        <Steps mode={mode} />
        <Heading title="Welcome back">
          Enter your email. We&apos;ll send you to single sign-on if your company uses it.
        </Heading>
        <form onSubmit={handleIdentifySubmit} className="space-y-4">
          {feedback}
          {emailField}
          <button type="submit" disabled={isLoading} className={AUTH_PRIMARY}>
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Checking…
              </>
            ) : (
              <>
                Continue
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </>
            )}
          </button>
        </form>
        {signUpFooter}
      </div>
    );
  }

  if (mode === "code") {
    return (
      <div className="flex w-full flex-col">
        <Steps mode={mode} />
        <Heading title="Two-factor sign-in">
          {useRecoveryCode
            ? "Enter one of the recovery codes you saved. Each one works once."
            : "Enter the 6-digit code from your authenticator app."}
        </Heading>
        <form onSubmit={handleCodeSubmit} className="space-y-4">
          {feedback}
          <div className="space-y-1.5">
            <label className={AUTH_LABEL} htmlFor="mfa-code">
              {useRecoveryCode ? "Recovery code" : "Authentication code"}
            </label>
            <div className="relative">
              <Smartphone className={AUTH_INPUT_ICON} aria-hidden="true" />
              <input
                id="mfa-code"
                autoFocus
                inputMode={useRecoveryCode ? "text" : "numeric"}
                autoComplete="one-time-code"
                autoCapitalize="characters"
                disabled={isLoading}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className={`${AUTH_INPUT} pl-9 font-mono tracking-[0.3em] placeholder:tracking-normal`}
                placeholder={useRecoveryCode ? undefined : "000000"}
                required
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={isLoading || !code.trim()}
            className={AUTH_PRIMARY}
          >
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Verifying...
              </>
            ) : (
              "Verify"
            )}
          </button>
          <div className="flex flex-wrap justify-between gap-2">
            <button
              type="button"
              onClick={() => {
                setUseRecoveryCode((value) => !value);
                setCode("");
                setError(null);
              }}
              disabled={isLoading}
              className={AUTH_MUTED_LINK}
            >
              {useRecoveryCode ? "Use the authenticator app" : "Use a recovery code"}
            </button>
            <button
              type="button"
              onClick={() => switchMode("password")}
              disabled={isLoading}
              className={AUTH_MUTED_LINK}
            >
              Start over
            </button>
          </div>
        </form>
      </div>
    );
  }

  if (mode === "sso") {
    return (
      <div className="flex w-full flex-col">
        <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-muted/60 text-primary shadow-inner-highlight">
          <KeyRound className="h-5 w-5" aria-hidden="true" />
        </div>
        <Heading title="Sign in with single sign-on">
          Enter your work email and we&apos;ll send you to your company&apos;s sign-in page
        </Heading>

        <form onSubmit={handleSsoSubmit} className="space-y-4">
          {feedback}
          {emailField}

          <button type="submit" disabled={isLoading} className={AUTH_PRIMARY}>
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
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
              className={AUTH_MUTED_LINK}
            >
              Sign in with a password instead
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="flex w-full flex-col">
      <Steps mode={mode} />
      <Heading title="Welcome back">Enter your email to sign in to your account</Heading>

      <form onSubmit={handlePasswordSubmit} className="space-y-4">
        {feedback}
        {emailField}

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className={AUTH_LABEL} htmlFor="password">
              Password
            </label>
            <Link to={ROUTES.FORGOT_PASSWORD} className={AUTH_MUTED_LINK}>
              Forgot your password?
            </Link>
          </div>
          <div className="relative">
            <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              id="password"
              placeholder="••••••••"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              autoFocus
              disabled={isLoading}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={`${AUTH_INPUT} pl-9 pr-10`}
              required
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              disabled={isLoading}
              aria-label={showPassword ? "Hide password" : "Show password"}
              className={AUTH_TRAILING_BUTTON}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        <button type="submit" disabled={isLoading} className={AUTH_PRIMARY}>
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Signing in...
            </>
          ) : (
            "Continue"
          )}
        </button>
      </form>

      <div className="relative my-5 select-none" aria-hidden="true">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t border-border/80" />
        </div>
        <div className="relative flex justify-center">
          <span className="bg-card px-2 text-[11px] uppercase tracking-[0.08em] text-muted-foreground dark:bg-zinc-900">or</span>
        </div>
      </div>

      <button
        type="button"
        onClick={() => switchMode("sso")}
        disabled={isLoading}
        className={AUTH_SECONDARY}
      >
        <KeyRound className="h-4 w-4" aria-hidden="true" />
        Continue with single sign-on
      </button>

      {signUpFooter}
    </div>
  );
};

export default Login;
