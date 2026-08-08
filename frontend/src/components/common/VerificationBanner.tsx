/**
 * Unverified-account banner for FlowPilot AI.
 *
 * An unverified user can sign in and see their own identity, but every
 * tenant-scoped request answers 403 (ARCH-03 §B.4). Without this banner that
 * reads as an application fault: the shell renders, the navigation is there,
 * and everything inside it fails for a reason the server has explained only in
 * a JSON body nobody sees.
 *
 * So the state is surfaced where the user already is, with the one action that
 * resolves it.
 */

import React from "react";
import { toast } from "sonner";
import { MailWarning } from "lucide-react";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { useAuthStore } from "@/store/useAuthStore";

export function VerificationBanner() {
  const user = useAuthStore((state) => state.user);
  const [sending, setSending] = React.useState(false);

  // Rendered from the user object rather than from a failed request, so it
  // appears on the first paint instead of after something has already broken.
  if (!user || user.email_verified_at) {
    return null;
  }

  const resend = async (): Promise<void> => {
    setSending(true);
    try {
      const result = await authApi.resendVerificationRequest();
      if (result.delivered) {
        toast.success(result.detail);
      } else {
        // 202 with delivered false: the token exists, only the send failed.
        // Not an error state for the account (R7).
        toast.warning(result.detail);
      }
    } catch (error: unknown) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not send the verification email.",
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm">
      <MailWarning className="h-4 w-4 shrink-0 text-amber-600" />
      <span className="text-amber-900 dark:text-amber-200">
        Verify <strong>{user.email}</strong> to unlock your workspaces.
      </span>
      <button
        type="button"
        onClick={() => void resend()}
        disabled={sending}
        className="ml-auto rounded-md border border-amber-600/40 px-3 py-1 text-xs font-medium text-amber-900 disabled:opacity-50 dark:text-amber-200"
      >
        {sending ? "Sending…" : "Resend link"}
      </button>
    </div>
  );
}

export default VerificationBanner;
