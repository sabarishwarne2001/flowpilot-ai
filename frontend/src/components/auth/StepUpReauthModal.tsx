import React, { useCallback, useEffect, useRef, useState } from "react";
import { ShieldAlert, X } from "lucide-react";

import apiClient from "@/services/api/client";
import { ApiError } from "@/services/api/errors";
import { useAuthStore } from "@/store/useAuthStore";
import { useSessionGuardStore } from "@/store/useSessionGuardStore";

/**
 * Step-up re-authentication.
 *
 * WHY A PASSWORD, AND NOT A REFRESH
 * =================================
 *
 * The obvious implementation of this modal is a button that calls
 * `/auth/refresh` and retries. It would not work, and it would fail in the
 * worst possible way: silently, by succeeding.
 *
 * `assert_recent_authentication` in `app/services/billing/portal_service.py`
 * reads the `auth_time` claim, and SEC-1 defines `auth_time` as *when a human
 * last presented a credential* — explicitly not `iat`. The `iat` fallback was
 * deleted rather than deprecated, and that deletion is the fix. So
 * `session_service.py` carries `authenticated_at` forward unchanged when it
 * rotates a token at line 328, and only a new session stamps a fresh value at
 * line 107.
 *
 * A refresh therefore returns a brand-new access token with the *same*,
 * still-stale `auth_time`. The retry gets refused identically, the modal
 * reopens, and the user is in a loop that no amount of clicking escapes.
 *
 * The only thing that restamps the clock is `POST /auth/login`. That is what
 * this does.
 *
 * WHAT "FAILS CLOSED" MEANS HERE
 * =============================
 *
 * Dismissing the modal does not retry, does not degrade to a weaker check, and
 * does not remember that the user was asked. The refused request stays
 * refused. The only path that replays it is a successful credential
 * presentation — and the replay is handed back by the store rather than held
 * in this component, so a component unmount between challenge and success
 * cannot strand it.
 *
 * WHAT THIS DOES NOT YET DO
 * =========================
 *
 * ARCH-16 gives an org an IdP and a `force_reauth_max_age_s` policy
 * (`app/models/identity.py`), and a federated user has no password here to
 * present. The correct step-up for them is a SAML `AuthnRequest` carrying
 * `ForceAuthn`, returning through `/api/v1/saml/acs`.
 *
 * `/api/v1/sso/start` exists and `saml.py` already reads
 * `force_reauth_max_age_s`, but it takes no parameter that requests a forced
 * re-authentication and no parameter that would bring the user back to the
 * action they were attempting. Until it does, a federated user reaching this
 * modal is told plainly what to do rather than shown a password field they
 * cannot fill. Guessing a query parameter here would produce a button that
 * looks like it works and silently completes an ordinary SSO round trip —
 * leaving `auth_time` untouched and the action still refused.
 */

const StepUpReauthModal: React.FC = () => {
  const challenge = useSessionGuardStore((state) => state.stepUp);
  const resolveStepUp = useSessionGuardStore((state) => state.resolveStepUp);
  const cancelStepUp = useSessionGuardStore((state) => state.cancelStepUp);

  const userEmail = useAuthStore((state) => state.user?.email ?? "");
  const setToken = useAuthStore((state) => state.setToken);

  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const passwordRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const open = challenge !== null;

  /* ------------------------------------------------------------------ */
  /* Lifecycle                                                           */
  /* ------------------------------------------------------------------ */

  useEffect(() => {
    if (!open) {
      // Clear on close rather than on open. Leaving a password in state after
      // the modal unmounts keeps it in the React tree and in any devtools
      // snapshot taken afterwards.
      setPassword("");
      setError(null);
      setSubmitting(false);
      return;
    }

    passwordRef.current?.focus();
  }, [open]);

  const handleCancel = useCallback(() => {
    if (submitting) {
      return;
    }
    cancelStepUp();
  }, [cancelStepUp, submitting]);

  // Escape closes, and closing fails closed. Bound at the document because the
  // dialog may not hold focus if the browser moved it during an autofill.
  useEffect(() => {
    if (!open) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleCancel();
        return;
      }

      // Focus trap. A modal that authenticates must not let Tab reach the
      // application behind it, where a click could dispatch the very action
      // being challenged.
      if (event.key !== "Tab" || !dialogRef.current) {
        return;
      }

      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      );

      if (focusable.length === 0) {
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, handleCancel]);

  /* ------------------------------------------------------------------ */
  /* Submission                                                          */
  /* ------------------------------------------------------------------ */

  const handleSubmit = useCallback(async () => {
    if (submitting || password.length === 0) {
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      // Form-encoded, matching the OAuth2 password flow `auth.py` expects.
      // `_skipStepUp` keeps the interceptor from challenging the challenge:
      // without it, a 401 here would raise a second step-up and the store's
      // "one at a time" rule would drop the real one.
      const body = new URLSearchParams({
        username: userEmail.trim(),
        password,
      });

      const response = await apiClient.post<{ access_token: string }>(
        "/auth/login",
        body,
        {
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            Accept: "application/json",
          },
          _skipStepUp: true,
        } as never,
      );

      setToken(response.data.access_token);

      // Close first, then replay. The replayed request surfaces its own errors,
      // and rendering those behind a modal that has not yet unmounted puts a
      // toast underneath an overlay.
      const retry = resolveStepUp();
      setPassword("");
      retry?.();
    } catch (caught) {
      const message =
        caught instanceof ApiError
          ? caught.message
          : "Could not verify your password. Try again.";

      setError(message);
      setPassword("");
      passwordRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }, [password, resolveStepUp, setToken, submitting, userEmail]);

  if (!challenge) {
    return null;
  }

  /* ------------------------------------------------------------------ */
  /* Render                                                              */
  /* ------------------------------------------------------------------ */

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      role="presentation"
      onMouseDown={(event) => {
        // Backdrop click closes. `mousedown` on the backdrop specifically, so a
        // drag that starts inside the dialog and releases outside does not
        // dismiss a half-typed password.
        if (event.target === event.currentTarget) {
          handleCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="stepup-title"
        aria-describedby="stepup-reason"
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive"
          >
            <ShieldAlert className="h-5 w-5" />
          </span>

          <div className="min-w-0 flex-1">
            <h2 id="stepup-title" className="text-lg font-semibold">
              Confirm it&apos;s you
            </h2>
            <p
              id="stepup-reason"
              className="mt-1 text-sm text-muted-foreground"
            >
              {challenge.reason}
            </p>
          </div>

          <button
            type="button"
            onClick={handleCancel}
            disabled={submitting}
            aria-label="Cancel"
            className="-mr-1 -mt-1 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 space-y-3">
          <label
            htmlFor="stepup-password"
            className="block text-sm font-medium"
          >
            Password for {userEmail || "your account"}
          </label>

          {/*
            No <form>. A nested form inside an application that already has one
            mounted produces a DOM validation error in React 19, and the Enter
            key is handled explicitly below regardless.
          */}
          <input
            ref={passwordRef}
            id="stepup-password"
            type="password"
            autoComplete="current-password"
            value={password}
            disabled={submitting}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void handleSubmit();
              }
            }}
            aria-invalid={error !== null}
            aria-errormessage={error ? "stepup-error" : undefined}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-2 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          />

          {error && (
            <p
              id="stepup-error"
              role="alert"
              className="text-sm text-destructive"
            >
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={handleCancel}
            disabled={submitting}
            className="rounded-md border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting || password.length === 0}
            className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Confirming…" : "Confirm"}
          </button>
        </div>

        <p className="mt-4 text-xs text-muted-foreground">
          Signing in through your company&apos;s identity provider? Cancel, sign
          in again from the login screen, and retry the action.
        </p>
      </div>
    </div>
  );
};

export default StepUpReauthModal;
