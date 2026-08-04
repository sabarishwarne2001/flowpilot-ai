import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { acceptInvitation, rejectInvitation } from "@/services/api/workspace";
import { ROUTES } from "@/constants/routes";

export const InvitationAcceptPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();

  const [status, setStatus] = useState<
    "verifying" | "accepted" | "rejected" | "expired" | "invalid" | "auth_required"
  >("verifying");
  const [message, setMessage] = useState("Verifying your invitation credentials...");

  // Accept Invitation Mutation
  const { mutateAsync: acceptMutation } = useMutation({
    mutationFn: acceptInvitation,
    onSuccess: () => {
      setStatus("accepted");
      toast.success("Invitation accepted successfully!");
      setMessage("You have successfully joined the workspace! Redirecting to your dashboard...");
      setTimeout(() => {
        navigate(ROUTES.DASHBOARD);
      }, 3000);
    },
  });

  // Reject/Decline Invitation Mutation
  const { mutateAsync: rejectMutation, isPending: isRejecting } = useMutation({
    mutationFn: rejectInvitation,
    onSuccess: () => {
      setStatus("rejected");
      toast.success("Invitation declined.");
      setMessage("You have declined the workspace invitation. You can safely close this page.");
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to decline the invitation.");
    },
  });

  useEffect(() => {
    if (!token) {
      setStatus("invalid");
      setMessage("The invitation token is missing. Please verify your invitation link.");
      return;
    }

    const attemptAutoAccept = async () => {
      try {
        await acceptMutation(token);
      } catch (err: any) {
        const errorType = err.response?.data?.error || err.error || "";
        const detail = err.response?.data?.detail || err.message || "";

        if (errorType === "InvitationExpiredError") {
          setStatus("expired");
          setMessage(detail || "This invitation link has expired.");
        } else if (
          errorType === "InvalidInvitationTokenError" ||
          errorType === "InvitationNotFoundError"
        ) {
          setStatus("invalid");
          setMessage(detail || "This invitation link is invalid or does not exist.");
        } else if (
          errorType === "InvitationAlreadyProcessedError" ||
          errorType === "InvitationAlreadyMemberError"
        ) {
          setStatus("accepted");
          setMessage("You are already a member of this workspace. Redirecting to dashboard...");
          setTimeout(() => navigate(ROUTES.DASHBOARD), 3000);
        } else if (errorType === "InvitationPermissionDeniedError") {
          // This represents that an active user account matching the invitation email is required
          setStatus("auth_required");
          setMessage(detail || "An active user account matching the invitation email is required.");
        } else {
          setStatus("invalid");
          setMessage(detail || "An error occurred while validating the invitation.");
        }
      }
    };

    attemptAutoAccept();
  }, [token, navigate, acceptMutation]);

  const handleDecline = async () => {
    if (!token) return;
    await rejectMutation(token);
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center text-foreground p-4">
      <div className="bg-card border border-border p-8 rounded-xl shadow-lg max-w-md w-full text-center space-y-6">
        <div className="flex justify-center">
          {status === "verifying" && (
            <svg className="animate-spin h-10 w-10 text-primary" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
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

        <div className="space-y-2">
          <h2 className="text-xl font-bold tracking-tight">
            {status === "verifying" && "Verifying Invitation"}
            {status === "accepted" && "Workspace Joined"}
            {status === "rejected" && "Invitation Declined"}
            {status === "expired" && "Invitation Expired"}
            {status === "invalid" && "Invalid Invitation Link"}
            {status === "auth_required" && "Account Match Required"}
          </h2>
          <p className="text-sm text-muted-foreground">{message}</p>
        </div>

        {status === "auth_required" && (
          <div className="space-y-3 pt-4">
            <button
              onClick={() =>
                navigate(`${ROUTES.LOGIN}?redirect=/invitations/accept?token=${token}`)
              }
              className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
            >
              Log In with Matching Account
            </button>
            <button
              onClick={() =>
                navigate(`${ROUTES.REGISTER}?redirect=/invitations/accept?token=${token}`)
              }
              className="w-full rounded-lg border border-border bg-background py-2 text-sm font-semibold text-foreground transition hover:bg-muted/50"
            >
              Sign Up with Matching Email
            </button>
            <div className="pt-2 border-t border-border/50">
              <button
                onClick={handleDecline}
                disabled={isRejecting}
                className="w-full text-xs text-destructive hover:underline font-medium"
              >
                {isRejecting ? "Declining..." : "Decline This Invitation"}
              </button>
            </div>
          </div>
        )}

        {(status === "expired" || status === "invalid" || status === "rejected") && (
          <div className="pt-4">
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
