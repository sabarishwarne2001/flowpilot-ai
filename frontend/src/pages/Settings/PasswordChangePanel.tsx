import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";

import { changePasswordRequest } from "@/services/api/auth";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * ARCH-29 Slice 2 — password change.
 *
 * This is the first password change UI the platform has had. `changePasswordRequest`
 * has existed since ARCH-03 with zero call sites, and ProfileSettings contained
 * no occurrence of the word "password" at all.
 *
 * THE TRAP THIS COMPONENT EXISTS TO NOT FALL INTO
 * ===============================================
 *
 * `POST /auth/change-password` revokes EVERY session for the user, including
 * the one making the request, and returns a fresh access token that
 * re-establishes this device alone. The old token is dead the instant the
 * response is written.
 *
 * So the naive wiring —
 *
 *     useMutation({ mutationFn: () => changePasswordRequest(a, b) })
 *
 * — signs the user out of their own account as a direct consequence of
 * securing it. They change their password, the next request 401s, and the app
 * bounces them to the login screen with no explanation. `setToken` in
 * `onSuccess` is not a nicety; it is the other half of the operation.
 *
 * The revocation itself is the point and is not a bug: changing a password is
 * how you evict someone who has your old one. This panel says so, because a
 * user who is not told will read "signed out on your phone" as a break-in.
 */

interface PasswordChangePanelProps {
  /** Rendered as guidance only; the backend owns the real policy. */
  readonly minimumLength?: number;
}

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return String(detail[0].msg);
  }
  return fallback;
}

export const PasswordChangePanel: React.FC<PasswordChangePanelProps> = ({
  minimumLength = 12,
}) => {
  const setToken = useAuthStore((state) => state.setToken);

  const [open, setOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState(false);

  const reset = (): void => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
  };

  const change = useMutation({
    mutationFn: () => changePasswordRequest(currentPassword, newPassword),
    onSuccess: (response) => {
      // Load-bearing. The server has already revoked the token this request
      // was made with; without re-seeding, the very next call 401s and the
      // user is ejected from the app by their own security action.
      setToken(response.access_token);

      setError(null);
      setChanged(true);
      setOpen(false);
      reset();
    },
    onError: (err) => {
      // Never leave a rejected password in a DOM node.
      reset();
      setError(
        detailOf(
          err,
          "That password couldn't be changed. Check your current password and try again.",
        ),
      );
    },
  });

  const mismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword;

  const tooShort =
    newPassword.length > 0 && newPassword.length < minimumLength;

  const unchanged =
    newPassword.length > 0 && newPassword === currentPassword;

  const ready =
    currentPassword.length > 0 &&
    newPassword.length >= minimumLength &&
    newPassword === confirmPassword &&
    !unchanged;

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <KeyRound
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h2 className="text-lg font-bold tracking-tight text-foreground">
              Password
            </h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Changing your password signs you out everywhere else. You stay
              signed in here.
            </p>
          </div>
        </div>
        {!open && (
          <button
            type="button"
            onClick={() => {
              setOpen(true);
              setChanged(false);
              setError(null);
            }}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-muted"
          >
            Change password
          </button>
        )}
      </div>

      {changed && !open && (
        <p
          role="status"
          className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-3 text-sm text-foreground"
        >
          <ShieldCheck
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <span>
            Password changed. Every other session was signed out — if you are
            signed in on a phone or another browser, you will need to sign in
            again there with the new password.
          </span>
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}

      {open && (
        <div className="mt-4 space-y-3 border-t border-border pt-4">
          <div>
            <label
              htmlFor="password-current"
              className="text-sm font-semibold text-foreground"
            >
              Current password
            </label>
            <input
              id="password-current"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            />
          </div>

          <div>
            <label
              htmlFor="password-new"
              className="text-sm font-semibold text-foreground"
            >
              New password
            </label>
            <input
              id="password-new"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              At least {minimumLength} characters.
            </p>
            {tooShort && (
              <p className="mt-1 text-xs text-destructive">
                Too short — {minimumLength} characters minimum.
              </p>
            )}
            {unchanged && (
              <p className="mt-1 text-xs text-destructive">
                That is your current password. Choose a different one.
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="password-confirm"
              className="text-sm font-semibold text-foreground"
            >
              Confirm new password
            </label>
            <input
              id="password-confirm"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
            />
            {mismatch && (
              <p className="mt-1 text-xs text-destructive">
                The two passwords do not match.
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => change.mutate()}
              disabled={!ready || change.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground disabled:opacity-60"
            >
              {change.isPending && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              )}
              Change password
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setError(null);
                reset();
              }}
              disabled={change.isPending}
              className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold text-foreground disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default PasswordChangePanel;
