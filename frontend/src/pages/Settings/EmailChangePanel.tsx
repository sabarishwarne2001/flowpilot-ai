import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Mail, ShieldAlert } from "lucide-react";

import {
  cancelEmailChange,
  requestEmailChange,
} from "@/services/api/emailChange";

interface Props {
  readonly currentEmail: string;
}

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {return detail;}
  if (Array.isArray(detail) && detail[0]?.msg) {return String(detail[0].msg);}
  return fallback;
}

export const EmailChangePanel: React.FC<Props> = ({ currentEmail }) => {
  const [open, setOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState<{ email: string; expiresAt: string } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);

  const request = useMutation({
    mutationFn: () =>
      requestEmailChange({
        current_password: password,
        new_email: newEmail.trim(),
      }),
    onSuccess: (result) => {
      setError(null);
      setCancelled(false);
      setPending({ email: result.new_email, expiresAt: result.expires_at });
      setOpen(false);
      setNewEmail("");
      setPassword("");
    },
    onError: (err) => {
      setPassword("");
      setError(
        detailOf(
          err,
          "That change couldn't be requested. Check your password and the new address.",
        ),
      );
    },
  });

  const withdraw = useMutation({
    mutationFn: cancelEmailChange,
    onSuccess: () => {
      setError(null);
      setPending(null);
      setCancelled(true);
    },
    onError: (err) => {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setPending(null);
        setCancelled(true);
        setError(null);
        return;
      }
      setError(detailOf(err, "That request couldn't be withdrawn."));
    },
  });

  return (
    <section className="rounded-xl border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-bold text-foreground">
          <Mail className="h-4 w-4 text-primary" />
          Email address
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Signed in as <strong>{currentEmail}</strong>. Changing it takes two
          steps — you&apos;ll confirm from the new address.
        </p>
      </header>

      <div className="space-y-3 p-4">
        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {error}
          </p>
        )}

        {cancelled && (
          <p role="status" className="text-sm text-muted-foreground">
            No email change is pending. Your address is unchanged.
          </p>
        )}

        {pending ? (
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <p className="text-sm font-semibold text-foreground">
              Waiting for you to confirm {pending.email}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              We sent a link there. Open it to finish — the link expires{" "}
              {new Date(pending.expiresAt).toLocaleString()}. Until you confirm,
              you keep signing in with {currentEmail}. Confirming signs you out
              everywhere, including here.
            </p>
            <button
              type="button"
              onClick={() => withdraw.mutate()}
              disabled={withdraw.isPending}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted disabled:opacity-60"
            >
              {withdraw.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Cancel this change
            </button>
          </div>
        ) : open ? (
          <div className="space-y-3">
            <div>
              <label htmlFor="new-email" className="text-sm font-semibold text-foreground">
                New email address
              </label>
              <input
                id="new-email"
                type="email"
                value={newEmail}
                onChange={(event) => setNewEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@example.com"
                className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Make sure you can read mail here — the confirmation link goes to
                this address, not your current one.
              </p>
            </div>

            <div>
              <label htmlFor="email-password" className="text-sm font-semibold text-foreground">
                Confirm your password
              </label>
              <input
                id="email-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                maxLength={255}
                className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Required for security verification.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => request.mutate()}
                disabled={
                  request.isPending ||
                  newEmail.trim().length === 0 ||
                  password.length === 0
                }
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground disabled:opacity-60"
              >
                {request.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Send confirmation link
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  setNewEmail("");
                  setPassword("");
                  setError(null);
                }}
                disabled={request.isPending}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground disabled:opacity-60"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => {
                setError(null);
                setCancelled(false);
                setOpen(true);
              }}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-muted"
            >
              Change email address…
            </button>
            <p className="text-xs text-muted-foreground">
              Started a change and reloaded the page? We can&apos;t show its
              status yet, but it may still be waiting — check the new inbox, or{" "}
              <button
                type="button"
                onClick={() => withdraw.mutate()}
                disabled={withdraw.isPending}
                className="underline underline-offset-2 disabled:opacity-60 text-foreground"
              >
                cancel any pending change
              </button>
              .
            </p>
          </div>
        )}
      </div>
    </section>
  );
};

export default EmailChangePanel;
