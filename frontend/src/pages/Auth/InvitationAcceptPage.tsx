import React, { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ROUTES } from "@/constants/routes";
import { workspacePath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";
import {
  acceptInvitation,
  previewInvitation,
  rejectInvitation,
} from "@/services/api/invitations";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * Invitation acceptance page for FlowPilot AI.
 *
 * ARCH-01 closed a security hole here. Accept and reject previously took only
 * a token and the server resolved the user by invitation.email, so anyone
 * holding a forwarded link could accept on the invitee's behalf — and reject
 * in particular gave a token holder a denial of service on an invitation the
 * recipient had never seen.
 *
 * Both operations now require an authenticated session whose email matches the
 * invited address. The token identifies the invitation; the session identifies
 * the actor.
 *
 * Preview stays public: a recipient must see who invited them and to what
 * before creating an account.
 *
 * Error handling also changed shape. The previous implementation read
 * err.response.data.error, a field the backend has never emitted, and compared
 * it against Python exception class names. Every branch was therefore dead,
 * and every failure fell through to a generic message. Branching is now on the
 * stable `code` from the ARCH-01 error envelope.
 */

type Phase =
  | "preview"
  | "accepted"
  | "rejected"
  | "expired"
  | "invalid"
  | "auth_required"
  | "email_mismatch";

export const InvitationAcceptPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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

  /**
   * Resolves the phase from token presence, preview outcome, and session.
   *
   * Derived rather than accumulated in state: the previous implementation set
   * status from an effect watching the query, which meant an in-flight refetch
   * could leave the UI showing a stale outcome.
   */
  const resolvedPhase: Phase = useMemo(() => {
    if (phase) {
      return phase;
    }

    if (!token) {
      return "invalid";
    }

    if (previewError instanceof ApiError) {
      return previewError.code === API_ERROR_CODES.INVITATION_EXPIRED
        ? "expired"
        : "invalid";
    }

    if (previewError) {
      return "invalid";
    }

    // Accepting requires a session. Surfacing that before the user clicks —
    // rather than after a 401 — means they are not told "join this workspace"
    // and then handed an error for doing so.
    if (preview && !isAuthenticated) {
      return "auth_required";
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

  const previewMessage = useMemo(() => {
    if (previewError instanceof ApiError) {
      return previewError.message;
    }
    if (!token) {
      return "The secure invitation token is missing from the link.";
    }
    return "This invitation is invalid or has expired.";
  }, [previewError, token]);

  const { mutateAsync: acceptMutation, isPending: isAccepting } = useMutation({
    mutationFn: () => acceptInvitation(token as string),
    onSuccess: async (result) => {
      setPhase("accepted");
      toast.success("Invitation accepted. Welcome aboard.");

      // The bootstrap context now includes a tenant it did not before, so it
      // must be refetched before navigating — TenantGuard would otherwise
      // resolve against a stale context and bounce the user to onboarding.
      await queryClient.invalidateQueries({ queryKey: ["me"] });

      navigate(workspacePath(result.organization_slug, result.workspace_slug), {
        replace: true,
      });
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

  const handleAuthRedirect = (targetRoute: string): void => {
    const acceptUrl = `${ROUTES.INVITATION_ACCEPT}?token=${encodeURIComponent(token ?? "")}`;
    navigate(`${targetRoute}?redirect=${encodeURIComponent(acceptUrl)}`);
  };

  const handleSwitchAccount = (): void => {
    useAuthStore.getState().clearAuth();
    handleAuthRedirect(ROUTES.LOGIN);
  };

  if (isLoadingPreview && !phase) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center text-foreground">
        <div className="flex flex-col items-center space-y-4">
          <svg className="animate-spin h-10 w-10 text-primary" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="text-sm text-muted-foreground font-semibold">Resolving invitation credentials...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center text-foreground p-4">
      <div className="bg-card border border-border p-8 rounded-xl shadow-lg max-w-md w-full text-center space-y-6">
        <div className="flex justify-center">
          {resolvedPhase === "preview" && (
            <svg className="h-12 w-12 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          )}

          {resolvedPhase === "accepted" && (
            <svg className="h-12 w-12 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}

          {resolvedPhase === "rejected" && (
            <svg className="h-12 w-12 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}

          {(resolvedPhase === "expired" || resolvedPhase === "invalid") && (
            <svg className="h-12 w-12 text-destructive" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          )}

          {(resolvedPhase === "auth_required" || resolvedPhase === "email_mismatch") && (
            <svg className="h-12 w-12 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          )}
        </div>

        {resolvedPhase === "preview" && preview && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">You've been invited!</h2>
              <p className="text-sm text-muted-foreground">
                <strong>{preview.inviter_email}</strong> has invited you to join the{" "}
                <strong>{preview.workspace_name}</strong> workspace at{" "}
                <strong>{preview.organization_name}</strong> as a{" "}
                <strong>{preview.role.toLowerCase()}</strong>.
              </p>
            </div>
            <div className="flex flex-col gap-2 pt-4">
              <button
                onClick={() => void acceptMutation()}
                disabled={isAccepting}
                className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {isAccepting ? "Joining..." : "Accept & Join Workspace"}
              </button>
              <button
                onClick={() => void rejectMutation()}
                disabled={isRejecting}
                className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50 disabled:opacity-50"
              >
                {isRejecting ? "Declining..." : "Decline Invitation"}
              </button>
            </div>
          </div>
        )}

        {resolvedPhase === "accepted" && (
          <div className="space-y-2">
            <h2 className="text-xl font-bold tracking-tight">Joined Workspace!</h2>
            <p className="text-sm text-muted-foreground">Taking you to your new workspace...</p>
          </div>
        )}

        {resolvedPhase === "rejected" && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Invitation Declined</h2>
              <p className="text-sm text-muted-foreground">You have declined this workspace invitation. You may safely close this page.</p>
            </div>
            <button
              onClick={() => navigate(ROUTES.LOGIN)}
              className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50"
            >
              Back to Login
            </button>
          </div>
        )}

        {resolvedPhase === "auth_required" && (
          <div className="space-y-3 pt-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Sign in to continue</h2>
              <p className="text-sm text-muted-foreground text-left">
                Accepting an invitation requires a signed-in account matching{" "}
                <strong>{preview?.invited_email}</strong>. This is what prevents anyone
                who receives a forwarded link from joining in your place.
              </p>
            </div>
            <button
              onClick={() => handleAuthRedirect(ROUTES.LOGIN)}
              className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
            >
              Log In with Matching Account
            </button>
            <button
              onClick={() => handleAuthRedirect(ROUTES.REGISTER)}
              className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50"
            >
              Sign Up with Matching Email
            </button>
          </div>
        )}

        {resolvedPhase === "email_mismatch" && (
          <div className="space-y-3 pt-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Wrong account</h2>
              <p className="text-sm text-muted-foreground text-left">
                {errorMsg ||
                  `This invitation was sent to ${preview?.invited_email ?? "another address"}, but you are signed in as ${currentEmail ?? "a different account"}.`}
              </p>
            </div>
            <button
              onClick={handleSwitchAccount}
              className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
            >
              Sign out and switch account
            </button>
          </div>
        )}

        {(resolvedPhase === "expired" || resolvedPhase === "invalid") && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">
                {resolvedPhase === "expired" ? "Invitation Expired" : "Invalid Link"}
              </h2>
              <p className="text-sm text-muted-foreground">{errorMsg || previewMessage}</p>
            </div>
            <button
              onClick={() => navigate(ROUTES.LOGIN)}
              className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50"
            >
              Back to Login
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default InvitationAcceptPage;
