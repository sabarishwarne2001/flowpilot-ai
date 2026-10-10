import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Building2, Eye, EyeOff, Loader2, Lock, Mail } from "lucide-react";

import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ROUTES } from "@/constants/routes";
import { workspacePath } from "@/routes/tenantPaths";
import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import {
  acceptInvitation,
  previewInvitation,
  registerWithInvitation,
  rejectInvitation,
} from "@/services/api/invitations";
import { useAuthStore } from "@/store/useAuthStore";
import { AuthShell } from "@/components/auth/AuthShell";
import PasswordStrengthMeter from "@/components/auth/PasswordStrengthMeter";
import {
  AUTH_FIELD_ERROR,
  AUTH_INPUT,
  AUTH_INPUT_ICON,
  AUTH_LABEL,
  AUTH_LINK,
  AUTH_PRIMARY,
  AUTH_SECONDARY,
  AUTH_SUBTITLE,
  AUTH_TITLE,
  AUTH_TRAILING_BUTTON,
} from "@/components/auth/authStyles";
import { formatTimestamp } from "@/utils/displayTime";
import type { WorkspaceInvitationPreview } from "@/types/tenancy";

type Phase =
  | "preview"
  | "accepted"
  | "rejected"
  | "expired"
  | "invalid"
  | "signup"
  | "choose_account"
  | "auth_required"
  | "email_mismatch";

/** The server's minimum (app/core/password_policy.MIN_LENGTH); it checks strength too. */
const MIN_PASSWORD_LENGTH = 12;

const titleCase = (value: string): string =>
  value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();

/**
 * F-222. What the invitation is for, the same on every screen of the page:
 * the organization, who invited, the organization role and each workspace
 * with its role, and when the invitation runs out.
 */
const InvitationSummary: React.FC<{ readonly preview: WorkspaceInvitationPreview }> = ({ preview }) => (
  <div className="rounded-xl border border-border/80 bg-muted/30 p-4 text-left">
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Building2 className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 space-y-0.5">
        <p className="truncate text-sm font-semibold text-foreground">{preview.organization_name}</p>
        <p className="truncate text-xs text-muted-foreground" title={preview.inviter_email}>
          Invited by <span className="font-medium text-foreground">{preview.inviter_email}</span>
        </p>
      </div>
      <span className="ml-auto shrink-0 rounded-full border border-border bg-background px-2 py-0.5 text-[11px] font-semibold text-muted-foreground">
        {titleCase(preview.organization_role)}
      </span>
    </div>
    {preview.workspaces.length > 0 && (
      <ul aria-label="Workspaces" className="mt-3 space-y-1.5 border-t border-border/70 pt-3">
        {preview.workspaces.map((workspace) => (
          <li key={workspace.name} className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate text-foreground">{workspace.name}</span>
            <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
              {titleCase(workspace.role)}
            </span>
          </li>
        ))}
      </ul>
    )}
    <p className="mt-3 text-[11px] text-muted-foreground">
      This invitation expires {formatTimestamp(preview.expires_at)}.
    </p>
  </div>
);

const TOKEN_STASH_KEY = "flowpilot.invitation.token";
const TOKEN_STASH_TTL_MS = 30 * 60 * 1000;

const stashToken = (token: string): void => {
  try {
    window.sessionStorage.setItem(
      TOKEN_STASH_KEY,
      JSON.stringify({ token, at: Date.now() }),
    );
  } catch {
    // Storage unavailable
  }
};

const readStashedToken = (): string | null => {
  try {
    const raw = window.sessionStorage.getItem(TOKEN_STASH_KEY);
    if (!raw) { return null; }
    const parsed = JSON.parse(raw) as { token?: unknown; at?: unknown };
    if (
      typeof parsed.token !== "string" ||
      typeof parsed.at !== "number" ||
      Date.now() - parsed.at > TOKEN_STASH_TTL_MS
    ) {
      window.sessionStorage.removeItem(TOKEN_STASH_KEY);
      return null;
    }
    return parsed.token;
  } catch {
    return null;
  }
};

const clearStashedToken = (): void => {
  try {
    window.sessionStorage.removeItem(TOKEN_STASH_KEY);
  } catch {
    // Storage unavailable
  }
};

const stripCredentialFromAddressBar = (): void => {
  window.history.replaceState(null, "", window.location.pathname);
};

const takeInvitationToken = (): string | null => {
  const fragment = window.location.hash.replace(/^#/, "");
  if (fragment) {
    const fromFragment = new URLSearchParams(fragment).get("token");
    if (fromFragment) {
      stashToken(fromFragment);
      stripCredentialFromAddressBar();
      return fromFragment;
    }
  }
  return readStashedToken();
};

export const InvitationAcceptPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [token] = useState<string | null>(takeInvitationToken);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const currentEmail = useAuthStore((state) => state.user?.email ?? null);

  const [phase, setPhase] = useState<Phase | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const {
    data: preview,
    isLoading: isLoadingPreview,
    error: previewError,
  } = useQuery({
    queryKey: ["invitation", "preview", token],
    queryFn: () => previewInvitation(token as string),
    enabled: !!token,
    retry: false,
  });

  const resolvedPhase: Phase = useMemo(() => {
    if (phase) { return phase; }
    if (!token) { return "invalid"; }

    if (previewError instanceof ApiError) {
      return previewError.code === API_ERROR_CODES.INVITATION_EXPIRED
        ? "expired"
        : "invalid";
    }

    if (previewError) { return "invalid"; }

    // F-222. Signed out: someone with no account signs up right here; someone
    // with an account signs in and comes back.
    // F-226: whether the address has an account is unknown (null) when the
    // invitation went through the organization's own mail server; both ways in.
    if (preview && !isAuthenticated) {
      if (preview.has_account === true) { return "auth_required"; }
      if (preview.has_account === false) { return "signup"; }
      return "choose_account";
    }

    if (
      preview &&
      currentEmail &&
      currentEmail.trim().toLowerCase() !== preview.invited_email.trim().toLowerCase()
    ) {
      return "email_mismatch";
    }

    return "preview";
  }, [phase, token, previewError, preview, isAuthenticated, currentEmail]);

  useEffect(() => {
    if (
      resolvedPhase === "accepted" ||
      resolvedPhase === "rejected" ||
      resolvedPhase === "expired" ||
      resolvedPhase === "invalid"
    ) {
      clearStashedToken();
    }
  }, [resolvedPhase]);

  const previewMessage = useMemo(() => {
    if (previewError instanceof ApiError) { return previewError.message; }
    if (!token) {
      return "The secure invitation token is missing from the link. Open the link from your invitation email again.";
    }
    return "This invitation is invalid or has expired.";
  }, [previewError, token]);

  const { mutateAsync: acceptMutation, isPending: isAccepting } = useMutation({
    mutationFn: () => acceptInvitation(token as string),
    onSuccess: async (result) => {
      setPhase("accepted");
      clearStashedToken();
      toast.success("Invitation accepted. Welcome aboard.");

      await queryClient.invalidateQueries({ queryKey: ["me"] });

      // F-107: open the workspace the invitation granted. With none granted there is
      // nothing to open yet, so show the chooser (not a "no longer available" warning).
      navigate(
        result.workspace_slug
          ? workspacePath(result.organization_slug, result.workspace_slug)
          : ROUTES.WORKSPACES,
        { replace: true },
      );
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        if (error.code === API_ERROR_CODES.UNAUTHORIZED) {
          setPhase("auth_required");
          return;
        }
        if (error.code === API_ERROR_CODES.INVITATION_EMAIL_MISMATCH) {
          setPhase("email_mismatch");
          setErrorMsg(error.message);
          return;
        }
        if (error.code === API_ERROR_CODES.INVITATION_EXPIRED) {
          setPhase("expired");
          setErrorMsg(error.message);
          return;
        }
        toast.error(error.message);
        return;
      }
      toast.error("Failed to accept the invitation.");
    },
  });

  const { mutateAsync: rejectMutation, isPending: isRejecting } = useMutation({
    mutationFn: () => rejectInvitation(token as string),
    onSuccess: () => {
      setPhase("rejected");
      clearStashedToken();
      toast.success("Invitation declined.");
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        if (error.code === API_ERROR_CODES.UNAUTHORIZED) {
          setPhase("auth_required");
          return;
        }
        toast.error(error.message);
        return;
      }
      toast.error("Failed to decline the invitation.");
    },
  });

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [signupError, setSignupError] = useState<string | null>(null);
  const setToken = useAuthStore((state) => state.setToken);
  const setAuth = useAuthStore((state) => state.setAuth);

  const { mutate: signUp, isPending: isSigningUp } = useMutation({
    mutationFn: () => registerWithInvitation(token as string, password),
    onSuccess: async (result) => {
      // The same steps as a sign-in (Login.finishSignIn): the token, then the profile.
      setToken(result.access_token);
      const me = await authApi.getMeRequest();
      setAuth(me, result.access_token);
      setPhase("accepted");
      clearStashedToken();
      setPassword("");
      setConfirmPassword("");
      toast.success("Your account is ready. Welcome aboard.");
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      navigate(
        result.workspace_slug
          ? workspacePath(result.organization_slug, result.workspace_slug)
          : ROUTES.WORKSPACES,
        { replace: true },
      );
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        if (error.code === API_ERROR_CODES.INVITATION_ACCOUNT_EXISTS) {
          setPhase("auth_required");
          setErrorMsg(error.message);
          return;
        }
        if (error.code === API_ERROR_CODES.INVITATION_EXPIRED) {
          setPhase("expired");
          setErrorMsg(error.message);
          return;
        }
        if (error.code === API_ERROR_CODES.INVITATION_SIGNUP_UNAVAILABLE) {
          setPhase("choose_account");
          setErrorMsg(error.message);
          return;
        }
        setSignupError(error.message);
        return;
      }
      setSignupError("We could not create your account. Please try again.");
    },
  });

  const handleSignup = (event: React.FormEvent): void => {
    event.preventDefault();
    if (isSigningUp) { return; }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setSignupError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmPassword) {
      setSignupError("The two passwords do not match.");
      return;
    }
    setSignupError(null);
    signUp();
  };

  const handleAuthRedirect = (targetRoute: string, email?: string): void => {
    if (token) { stashToken(token); }
    const params = new URLSearchParams({ redirect: ROUTES.INVITATION_ACCEPT });
    if (email) { params.set("email", email); }
    navigate(`${targetRoute}?${params.toString()}`);
  };

  // F-224. Signing out here only cleared this tab: the server session, and the
  // refresh cookie, outlived it, so the "other" account was still signed in.
  const [isSwitching, setIsSwitching] = useState(false);
  const handleSwitchAccount = async (): Promise<void> => {
    setIsSwitching(true);
    if (token) { stashToken(token); }
    await authApi.logoutRequest();
    useAuthStore.getState().clearAuth();
    // F-227. Sign-in only when the invited address has an account; otherwise stay
    // here, signed out, where the page offers the sign-up (or both ways in).
    if (preview && preview.has_account !== true) {
      setErrorMsg("");
      setPhase(null);
      setIsSwitching(false);
      return;
    }
    handleAuthRedirect(ROUTES.LOGIN, preview?.invited_email);
  };

  if (isLoadingPreview && !phase) {
    return (
      <AuthShell>
        <div className="flex flex-col items-center space-y-4 py-6">
          <svg className="animate-spin h-10 w-10 text-primary" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="text-sm text-muted-foreground">Resolving invitation credentials...</span>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <div className="w-full space-y-6">
        {resolvedPhase === "preview" && preview && (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>Join {preview.organization_name}</h1>
              <p className={AUTH_SUBTITLE}>
                You were invited as <strong className="text-foreground">{preview.invited_email}</strong>.
              </p>
            </div>
            <InvitationSummary preview={preview} />
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => void acceptMutation()}
                disabled={isAccepting}
                className={AUTH_PRIMARY}
              >
                {isAccepting ? "Joining..." : "Accept and join"}
              </button>
              <button
                type="button"
                onClick={() => void rejectMutation()}
                disabled={isRejecting}
                className={AUTH_SECONDARY}
              >
                {isRejecting ? "Declining..." : "Decline invitation"}
              </button>
            </div>
          </div>
        )}

        {resolvedPhase === "signup" && preview && (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>Join {preview.organization_name}</h1>
              <p className={AUTH_SUBTITLE}>
                Create your FlowPilot account to accept. The invitation confirms your
                address, so there is no separate verification email.
              </p>
            </div>
            <InvitationSummary preview={preview} />
            <form noValidate onSubmit={handleSignup} className="space-y-4 text-left">
              {signupError && (
                <p role="alert" className={AUTH_FIELD_ERROR}>
                  {signupError}
                </p>
              )}
              <div className="space-y-1.5">
                <label htmlFor="invitation-email" className={AUTH_LABEL}>
                  Email
                </label>
                <div className="relative">
                  <Mail className={AUTH_INPUT_ICON} aria-hidden="true" />
                  <input
                    id="invitation-email"
                    type="email"
                    value={preview.invited_email}
                    readOnly
                    aria-readonly="true"
                    aria-describedby="invitation-email-note"
                    className={`${AUTH_INPUT} cursor-not-allowed bg-muted/50 pl-9 pr-9 text-muted-foreground`}
                  />
                  <Lock
                    className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/80"
                    aria-hidden="true"
                  />
                </div>
                <p id="invitation-email-note" className="text-[11px] text-muted-foreground">
                  The address this invitation was sent to. It cannot be changed here.
                </p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="invitation-password" className={AUTH_LABEL}>
                  Password
                </label>
                <div className="relative">
                  <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
                  <input
                    id="invitation-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={isSigningUp}
                    className={`${AUTH_INPUT} pl-9 pr-10`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    tabIndex={-1}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className={AUTH_TRAILING_BUTTON}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                <PasswordStrengthMeter password={password} userInputs={[preview.invited_email]} />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="invitation-confirm-password" className={AUTH_LABEL}>
                  Confirm password
                </label>
                <div className="relative">
                  <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
                  <input
                    id="invitation-confirm-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    disabled={isSigningUp}
                    className={`${AUTH_INPUT} pl-9`}
                  />
                </div>
              </div>
              <button type="submit" disabled={isSigningUp} className={AUTH_PRIMARY}>
                {isSigningUp ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Creating your account...
                  </>
                ) : (
                  <>
                    Create account and join
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                  </>
                )}
              </button>
            </form>
          </div>
        )}

        {resolvedPhase === "auth_required" && (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>
                {preview ? `Sign in to join ${preview.organization_name}` : "Sign in to continue"}
              </h1>
              <p className={AUTH_SUBTITLE}>
                {errorMsg ||
                  `${preview?.invited_email ?? "The invited address"} already has a FlowPilot account. Sign in with it to accept.`}
              </p>
            </div>
            {preview && <InvitationSummary preview={preview} />}
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => handleAuthRedirect(ROUTES.LOGIN, preview?.invited_email)}
                className={AUTH_PRIMARY}
              >
                {preview ? `Sign in as ${preview.invited_email}` : "Sign in"}
              </button>
              <Link to={ROUTES.FORGOT_PASSWORD} className={`text-center text-[13px] ${AUTH_LINK}`}>
                Forgot your password?
              </Link>
            </div>
          </div>
        )}

        {resolvedPhase === "choose_account" && preview && (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>Join {preview.organization_name}</h1>
              <p className={AUTH_SUBTITLE}>
                {errorMsg ||
                  `Sign in with ${preview.invited_email}, or create a FlowPilot account for it. A new account confirms the address with a verification email first.`}
              </p>
            </div>
            <InvitationSummary preview={preview} />
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => handleAuthRedirect(ROUTES.LOGIN, preview.invited_email)}
                className={AUTH_PRIMARY}
              >
                {`Sign in as ${preview.invited_email}`}
              </button>
              <button
                type="button"
                onClick={() => handleAuthRedirect(ROUTES.REGISTER, preview.invited_email)}
                className={AUTH_SECONDARY}
              >
                Create an account
              </button>
            </div>
          </div>
        )}

        {resolvedPhase === "email_mismatch" && (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>Wrong account</h1>
              <p className={AUTH_SUBTITLE}>
                {errorMsg ||
                  `This invitation was sent to ${preview?.invited_email ?? "another address"}, but you are signed in as ${currentEmail ?? "a different account"}.`}
              </p>
            </div>
            {preview && <InvitationSummary preview={preview} />}
            <button
              type="button"
              onClick={() => void handleSwitchAccount()}
              disabled={isSwitching}
              className={AUTH_PRIMARY}
            >
              {isSwitching ? "Signing out..." : "Sign out and switch account"}
            </button>
          </div>
        )}

        {resolvedPhase === "accepted" && (
          <div className="space-y-1.5 text-center">
            <h1 className={AUTH_TITLE}>You&apos;re in</h1>
            <p className={AUTH_SUBTITLE}>Taking you to your workspace...</p>
          </div>
        )}

        {resolvedPhase === "rejected" && (
          <div className="space-y-5 text-center">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>Invitation declined</h1>
              <p className={AUTH_SUBTITLE}>You declined this invitation. You can close this page.</p>
            </div>
            <button type="button" onClick={() => navigate(ROUTES.LOGIN)} className={AUTH_SECONDARY}>
              Back to sign in
            </button>
          </div>
        )}

        {(resolvedPhase === "expired" || resolvedPhase === "invalid") && (
          <div className="space-y-5 text-center">
            <div className="space-y-1.5">
              <h1 className={AUTH_TITLE}>
                {resolvedPhase === "expired" ? "Invitation expired" : "Invalid link"}
              </h1>
              <p className={AUTH_SUBTITLE}>{errorMsg || previewMessage}</p>
            </div>
            <button type="button" onClick={() => navigate(ROUTES.LOGIN)} className={AUTH_SECONDARY}>
              Back to sign in
            </button>
          </div>
        )}
      </div>
    </AuthShell>
  );
};

export default InvitationAcceptPage;
