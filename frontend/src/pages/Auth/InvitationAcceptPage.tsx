import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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

type Phase =
  | "preview"
  | "accepted"
  | "rejected"
  | "expired"
  | "invalid"
  | "auth_required"
  | "email_mismatch";

const TOKEN_STASH_KEY = "flowpilot.invitation.token";
const TOKEN_STASH_TTL_MS = 30 * 60 * 1000;

const stashToken = (token: string): void => {
  try {
    window.sessionStorage.setItem(
      TOKEN_STASH_KEY,
      JSON.stringify({ token, at: Date.now() }),
    );
  } catch {}
};

const readStashedToken = (): string | null => {
  try {
    const raw = window.sessionStorage.getItem(TOKEN_STASH_KEY);
    if (!raw) return null;
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
  } catch {}
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
    if (phase) return phase;
    if (!token) return "invalid";

    if (previewError instanceof ApiError) {
      return previewError.code === API_ERROR_CODES.INVITATION_EXPIRED
        ? "expired"
        : "invalid";
    }

    if (previewError) return "invalid";

    if (preview && !isAuthenticated) return "auth_required";

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
    if (previewError instanceof ApiError) return previewError.message;
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

      navigate(workspacePath(result.organization_slug, result.workspace_slug || "default"), {
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

  const handleAuthRedirect = (targetRoute: string): void => {
    if (token) stashToken(token);
    navigate(
      `${targetRoute}?redirect=${encodeURIComponent(ROUTES.INVITATION_ACCEPT)}`,
    );
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

  // Safe fallback for role display to prevent undefined.toLowerCase() crash
  const roleDisplay = (preview?.role || (preview as any)?.organization_role || "member").toString().toLowerCase();

  return (
    <div className="min-h-screen bg-background flex items-center justify-center text-foreground p-4">
      <div className="bg-card border border-border p-8 rounded-xl shadow-lg max-w-md w-full text-center space-y-6">
        {resolvedPhase === "preview" && preview && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">You've been invited!</h2>
              <p className="text-sm text-muted-foreground">
                <strong>{preview.inviter_email}</strong> has invited you to join the{" "}
                <strong>{preview.organization_name}</strong> organization as a{" "}
                <strong>{roleDisplay}</strong>.
              </p>
            </div>
            <div className="flex flex-col gap-2 pt-4">
              <button
                onClick={() => void acceptMutation()}
                disabled={isAccepting}
                className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {isAccepting ? "Joining..." : "Accept & Join Organization"}
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
            <h2 className="text-xl font-bold tracking-tight">Joined Organization!</h2>
            <p className="text-sm text-muted-foreground">Taking you to your workspace...</p>
          </div>
        )}

        {resolvedPhase === "rejected" && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Invitation Declined</h2>
              <p className="text-sm text-muted-foreground">You have declined this invitation. You may safely close this page.</p>
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
                <strong>{preview?.invited_email}</strong>.
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
