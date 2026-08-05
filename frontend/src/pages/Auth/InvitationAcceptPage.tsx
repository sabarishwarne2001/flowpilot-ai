import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { previewInvitation, acceptInvitation, rejectInvitation } from "@/services/api/workspace";
import { ROUTES } from "@/constants/routes";

export const InvitationAcceptPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [status, setStatus] = useState<
    "previewing" | "accepted" | "rejected" | "expired" | "invalid" | "auth_required"
  >("previewing");
  const [errorMsg, setErrorMsg] = useState("");

  // Fetch Invitation Preview Details
  const { data: preview, isLoading: isLoadingPreview, error: previewError } = useQuery({
    queryKey: ["invitation_preview", token],
    queryFn: () => previewInvitation(token || ""),
    enabled: !!token,
    retry: false,
  });

  // Accept Invitation Mutation
  const { mutateAsync: acceptMutation, isPending: isAccepting } = useMutation({
    mutationFn: acceptInvitation,
    onSuccess: async () => {
      setStatus("accepted");
      toast.success("Invitation accepted! Welcome aboard.");
      // Standardize S2 cache invalidations
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspace"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace_members"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace_membership_me"] }),
      ]);
      setTimeout(() => navigate(ROUTES.DASHBOARD), 3000);
    },
    onError: (err: any) => {
      const errorType = err.response?.data?.error || "";
      if (errorType === "InvitationPermissionDeniedError") {
        setStatus("auth_required");
      } else {
        toast.error(err.response?.data?.detail || "Failed to accept the invitation.");
      }
    },
  });

  // Reject Invitation Mutation
  const { mutateAsync: rejectMutation, isPending: isRejecting } = useMutation({
    mutationFn: rejectInvitation,
    onSuccess: () => {
      setStatus("rejected");
      toast.success("Invitation declined.");
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to decline the invitation.");
    },
  });

  useEffect(() => {
    if (!token) {
      setStatus("invalid");
      setErrorMsg("The secure invitation token is missing from the link.");
      return;
    }

    if (previewError) {
      const err = previewError as any;
      const errorType = err.response?.data?.error || "";
      const detail = err.response?.data?.detail || "Invitation is invalid or has expired.";

      if (errorType === "InvitationExpiredError") {
        setStatus("expired");
      } else {
        setStatus("invalid");
      }
      setErrorMsg(detail);
    }
  }, [token, previewError]);

  const handleAccept = async () => {
    if (!token) return;
    await acceptMutation(token);
  };

  const handleDecline = async () => {
    if (!token) return;
    await rejectMutation(token);
  };

  const handleAuthRedirect = (targetRoute: string) => {
    const acceptUrl = `/invitations/accept?token=${token}`;
    navigate(`${targetRoute}?redirect=${encodeURIComponent(acceptUrl)}`, {
      state: { from: { pathname: "/invitations/accept", search: `?token=${token}` } }
    });
  };

  const isPageLoading = isLoadingPreview && status === "previewing";

  if (isPageLoading) {
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
          {status === "previewing" && (
            <svg className="h-12 w-12 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          )}

          {status === "accepted" && (
            <svg className="h-12 w-12 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}

          {status === "rejected" && (
            <svg className="h-12 w-12 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}

          {(status === "expired" || status === "invalid") && (
            <svg className="h-12 w-12 text-destructive" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          )}

          {status === "auth_required" && (
            <svg className="h-12 w-12 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          )}
        </div>

        {status === "previewing" && preview && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">You've been invited!</h2>
              <p className="text-sm text-muted-foreground">
                <strong>{preview.inviter_email}</strong> has invited you to join the <strong>{preview.workspace_name}</strong> workspace as a <strong>{preview.role}</strong>.
              </p>
            </div>
            <div className="flex flex-col gap-2 pt-4">
              <button
                onClick={handleAccept}
                disabled={isAccepting}
                className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {isAccepting ? "Joining..." : "Accept & Join Workspace"}
              </button>
              <button
                onClick={handleDecline}
                disabled={isRejecting}
                className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50 disabled:opacity-50"
              >
                {isRejecting ? "Declining..." : "Decline Invitation"}
              </button>
            </div>
          </div>
        )}

        {status === "accepted" && (
          <div className="space-y-2">
            <h2 className="text-xl font-bold tracking-tight">Joined Workspace!</h2>
            <p className="text-sm text-muted-foreground">Successfully onboarded. Redirecting to your dashboard...</p>
          </div>
        )}

        {status === "rejected" && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Invitation Declined</h2>
              <p className="text-sm text-muted-foreground">You have rejected the workspace invitation. You may safely close this page.</p>
            </div>
            <button
              onClick={() => navigate(ROUTES.LOGIN)}
              className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50"
            >
              Back to Login
            </button>
          </div>
        )}

        {status === "auth_required" && (
          <div className="space-y-3 pt-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">Authentication Required</h2>
              <p className="text-sm text-muted-foreground text-left">
                An active user account matching <strong>{preview?.invited_email}</strong> is required to accept this invitation.
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

        {(status === "expired" || status === "invalid") && (
          <div className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-xl font-bold tracking-tight">
                {status === "expired" ? "Invitation Expired" : "Invalid Link"}
              </h2>
              <p className="text-sm text-muted-foreground">{errorMsg}</p>
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
